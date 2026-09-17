#!/usr/bin/env python3
"""Validate the dashboards and provisioning files in this repository.

Checks every dashboards/*.json file:

  * parses as JSON
  * has uid, title, description and a numeric schemaVersion (>= 36, Grafana 9+)
  * declares the DS_PROMETHEUS import input (__inputs) and a matching
    datasource templating variable, so it imports cleanly from grafana.com
  * never hard-codes a data source uid: every prometheus datasource reference
    must be ${DS_PROMETHEUS}
  * panel ids are unique (including panels nested inside rows)
  * gridPos is present on every panel

And every YAML file under provisioning/ (plus docker-compose.yaml and
prometheus/): parses (needs PyYAML), and no dashboard provider combines
folder/folderUid with foldersFromFilesStructure (Grafana rejects that).

Usage: python3 scripts/validate.py      (exit code 0 = all checks passed)
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_DS_UIDS = {"${DS_PROMETHEUS}"}
ALLOWED_DS_TYPES = {"grafana", "datasource"}  # built-in annotations etc.
MIN_SCHEMA_VERSION = 36

failures, warnings, checks = [], [], 0


def fail(msg):
    failures.append(msg)
    print(f"FAIL  {msg}")


def warn(msg):
    warnings.append(msg)
    print(f"WARN  {msg}")


def ok(msg):
    global checks
    checks += 1
    print(f"  ok  {msg}")


def walk(node, path=""):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


def all_panels(panels):
    for p in panels:
        yield p
        yield from all_panels(p.get("panels", []))


files = sorted((ROOT / "dashboards").glob("*.json"))
if files:
    ok(f"found {len(files)} dashboard(s)")
else:
    fail("no dashboards/*.json files found")

uids = {}
for f in files:
    label = f"dashboards/{f.name}"
    try:
        d = json.loads(f.read_text())
        ok(f"{label}: valid JSON")
    except json.JSONDecodeError as e:
        fail(f"{label}: invalid JSON: {e}")
        continue

    for key in ("uid", "title", "description"):
        if d.get(key):
            ok(f"{label}: has {key}")
        else:
            fail(f"{label}: missing {key}")
    if d.get("uid") in uids:
        fail(f"{label}: uid {d['uid']!r} also used by {uids[d['uid']]}")
    uids[d.get("uid")] = label

    sv = d.get("schemaVersion")
    if isinstance(sv, int) and sv >= MIN_SCHEMA_VERSION:
        ok(f"{label}: schemaVersion {sv}")
    else:
        fail(f"{label}: schemaVersion {sv!r} missing or < {MIN_SCHEMA_VERSION}")

    inputs = {i.get("name"): i for i in d.get("__inputs", [])}
    ds_input = inputs.get("DS_PROMETHEUS")
    if ds_input and ds_input.get("type") == "datasource" and ds_input.get("pluginId") == "prometheus":
        ok(f"{label}: __inputs DS_PROMETHEUS (prometheus datasource)")
    else:
        fail(f"{label}: missing __inputs entry DS_PROMETHEUS of type datasource/prometheus")

    ds_vars = [v for v in d.get("templating", {}).get("list", [])
               if v.get("type") == "datasource" and v.get("name") == "DS_PROMETHEUS"]
    if ds_vars and ds_vars[0].get("query") == "prometheus":
        ok(f"{label}: DS_PROMETHEUS datasource variable")
    else:
        fail(f"{label}: missing DS_PROMETHEUS datasource templating variable")

    bad = 0
    for path, node in walk(d):
        ds = node.get("datasource")
        if isinstance(ds, str) and ds and not ds.startswith("${"):
            fail(f"{label}: legacy string datasource {ds!r} at {path or '<root>'}")
            bad += 1
        if not isinstance(ds, dict):
            continue
        dtype, duid = ds.get("type"), ds.get("uid")
        if dtype in ALLOWED_DS_TYPES or duid in (None, "") or duid in ALLOWED_DS_UIDS:
            continue
        fail(f"{label}: hard-coded data source uid {duid!r} at {path or '<root>'}")
        bad += 1
    if not bad:
        ok(f"{label}: all datasource uids use ${{DS_PROMETHEUS}}")

    panels = list(all_panels(d.get("panels", [])))
    ids = [p.get("id") for p in panels]
    dupes = sorted({i for i in ids if ids.count(i) > 1}, key=str)
    if None in ids:
        fail(f"{label}: panel without id")
    elif dupes:
        fail(f"{label}: duplicate panel ids {dupes}")
    else:
        ok(f"{label}: {len(panels)} panels, ids unique")
    if all(isinstance(p.get("gridPos"), dict) for p in panels):
        ok(f"{label}: gridPos on every panel")
    else:
        fail(f"{label}: panel(s) without gridPos")

try:
    import yaml
except ImportError:
    yaml = None
    warn("PyYAML not installed - skipping YAML checks (pip install pyyaml)")

if yaml:
    yaml_files = sorted(
        list((ROOT / "provisioning").rglob("*.y*ml"))
        + list((ROOT / "prometheus").rglob("*.y*ml"))
        + [p for p in (ROOT / "docker-compose.yaml",) if p.exists()]
    )
    for f in yaml_files:
        rel = f.relative_to(ROOT)
        text = f.read_text()
        try:
            docs = list(yaml.safe_load_all(text))
            ok(f"{rel}: valid YAML ({len(docs)} doc(s))")
        except yaml.YAMLError as e:
            fail(f"{rel}: invalid YAML: {e}")
            continue
        for doc in docs:
            for prov in (doc or {}).get("providers", []) if isinstance(doc, dict) else []:
                opts = prov.get("options") or {}
                if opts.get("foldersFromFilesStructure") and (prov.get("folder") or prov.get("folderUid")):
                    fail(f"{rel}: provider {prov.get('name')!r} combines folder/folderUid "
                         "with foldersFromFilesStructure")
                else:
                    ok(f"{rel}: provider {prov.get('name')!r} folder settings valid")

print()
print(f"{checks} checks passed, {len(warnings)} warning(s), {len(failures)} failure(s)")
if failures:
    sys.exit(1)
print("All validation checks passed.")
