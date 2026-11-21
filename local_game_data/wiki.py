"""Compatibility and lossless merging for the existing Huiji tabx writers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys
import re

from .dataset import Dataset, write_json


ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {"Card": "cards", "Relic": "relics", "Potion": "potions", "Power": "powers", "Monster": "monsters", "Event": "events", "Keyword": "keywords"}


def baseline_path(page):
    directory = Path(os.environ.get("STS2_WIKI_BASELINE", ROOT / "local_data/baseline"))
    name = page.removeprefix("Data:")
    choices = [directory / (name + ".json"), directory / (name.replace(".tabx", "") + ".json")]
    category = CATEGORIES.get(name.removesuffix(".tabx"))
    if category:
        versions = [p for p in directory.glob(category + "_*.json") if not p.name.endswith(".report.json")]
        choices.extend(sorted(versions, key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.stem)), reverse=True))
    return next((p for p in choices if p.is_file()), None)


def load_tabx(page):
    path = baseline_path(page)
    if path:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return None


def records(document):
    if document is None:
        return {}
    fields = [f["name"] for f in document["schema"]["fields"]]
    if "id" not in fields or len(fields) != len(set(fields)):
        raise ValueError("Wiki baseline must have unique field names and an id field")
    result = {}
    for row in document["data"]:
        if len(row) != len(fields):
            raise ValueError("Wiki baseline row does not match its schema")
        record = dict(zip(fields, row))
        if not record["id"] or record["id"] in result:
            raise ValueError(f"Missing or duplicate Wiki baseline id: {record['id']}")
        result[record["id"]] = record
    return result


def page_names():
    directory = Path(os.environ.get("STS2_WIKI_BASELINE", ROOT / "local_data/baseline"))
    names = set()
    file = directory / "page_names.json"
    if file.is_file():
        names.update(json.loads(file.read_text(encoding="utf-8-sig")))
    for page in CATEGORIES:
        for row in records(load_tabx(f"Data:{page}.tabx")).values():
            if row.get("page"):
                names.add(row["page"])
    return names


def write_tabx(filename, content):
    """Keep the original schema, retain baseline rows/values on extraction gaps.

    Incomplete new records are withheld. A table without the baseline needed to
    preserve editorial fields is explicitly marked as a candidate in a sidecar.
    """
    document = json.loads(content) if isinstance(content, str) else content
    fields = [f["name"] for f in document["schema"]["fields"]]
    category = Path(filename).name.split("_", 1)[0]
    category = "keywords" if category == "keyword" else category
    page = next(key for key, value in CATEGORIES.items() if value == category)
    baseline = records(load_tabx(f"Data:{page}.tabx"))
    dataset = Dataset(os.environ.get("STS2_DATASET", ROOT / "local_data/dataset.json"))
    source_rows = dataset.items["cards"] if category == "cards" else dataset.get(category)
    source = {}
    for row in source_rows:
        key = row["id"].lower() + ("_upgrade" if row.get("upgrades") == 1 else "")
        source[key] = row
    merged, withheld, preserved = {}, [], []
    for row in document["data"]:
        record = dict(zip(fields, row))
        key = record["id"]
        errors = source.get(key, {}).get("_errors", {})
        # These errors invalidate the rendered row even when a scalar field
        # happened to have a default value in the legacy formatter.
        relevant = set(errors) & (set(fields) | {"constructor", "upgrade", "keywords"})
        if category == "monsters":
            relevant.update(set(errors) & {"min_hp", "max_hp", "min_hp_ascension", "max_hp_ascension"})
        if category == "potions" and "pool" in errors:
            relevant.add("pool")
        if relevant:
            withheld.append({"id": key, "fields": sorted(relevant), "errors": errors})
            if key in baseline:
                merged[key] = baseline[key]
                preserved.append(key)
            continue
        if key in baseline:
            old = baseline[key]
            # Page names are editorial identifiers, not game translations.
            if "page" in fields and old.get("page"):
                record["page"] = old["page"]
            if category == "monsters":
                for field in ("tier", "power", "stage", "note"):
                    if field in old:
                        record[field] = old[field]
        merged[key] = record
    # Missing models can be a decompilation gap rather than a game removal.
    for key, row in baseline.items():
        if key not in merged:
            merged[key] = row
            preserved.append(key)
    document["data"] = [[r.get(f) for f in fields] for r in merged.values()]
    write_json(filename, document)
    missing_editorial_baseline = category in ("cards", "monsters") and not baseline
    status = {"category": category, "rows": len(document["data"]), "withheld": withheld,
              "preserved_baseline_ids": sorted(set(preserved)),
              "editorial_baseline_missing": missing_editorial_baseline,
              "ready_to_publish": not withheld and not missing_editorial_baseline}
    write_json(str(filename) + ".report.json", status)
    if withheld or missing_editorial_baseline:
        print(f"{filename}: 候选数据，缺口 {len(withheld)} 条；保留旧条目 {len(set(preserved))} 条。详见同名 report.json。")


def export_all(dataset_path, output, baseline=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    old_cwd, old_path = Path.cwd(), list(sys.path)
    keys = ("STS2_DATASET", "STS2_OFFLINE", "STS2_WIKI_BASELINE")
    old_env = {k: os.environ.get(k) for k in keys}
    old_utils = sys.modules.get("utils")
    try:
        os.environ["STS2_DATASET"] = str(Path(dataset_path).resolve())
        os.environ["STS2_OFFLINE"] = "1"
        if baseline:
            os.environ["STS2_WIKI_BASELINE"] = str(Path(baseline).resolve())
        os.chdir(output)
        # Refresh a previously imported compatibility module when invoked twice.
        sys.path.insert(0, str(ROOT))
        sys.modules.pop("utils", None)
        for category in ("card", "relic", "potion", "power", "monster", "event", "keyword"):
            runpy.run_path(str(ROOT / category / ("update_" + category + ".py")), run_name="__main__")
    finally:
        os.chdir(old_cwd)
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.path[:] = old_path
        sys.modules.pop("utils", None)
        if old_utils is not None:
            sys.modules["utils"] = old_utils


def snapshot_wiki(output):
    """Explicit online read step; normal extraction/export never invokes it."""
    import mwclient
    username = os.environ.get("HUIJI_WIKI_USERNAME")
    password = os.environ.get("HUIJI_WIKI_PASSWORD")
    if not username or not password:
        raise ValueError("保存 Wiki 快照需要 HUIJI_WIKI_USERNAME、HUIJI_WIKI_PASSWORD；不会读取或修改游戏数据。")
    site = mwclient.Site("sts2.huijiwiki.com", custom_headers={"X-authkey": os.environ.get("HUIJI_WIKI_STS2_KEY", "")})
    site.login(username, password)
    for name in CATEGORIES:
        document = json.loads(site.pages[f"Data:{name}.tabx"].text())
        records(document)  # Validate before replacing the local cache.
        write_json(Path(output) / f"{name}.tabx.json", document)
    write_json(Path(output) / "page_names.json", [p.name for p in site.allpages()])
