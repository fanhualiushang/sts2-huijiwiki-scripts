"""Compatibility and lossless merging for the existing Huiji tabx writers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys
import re

from .dataset import Dataset, write_json
from .config import load_config
from .baseline import read_tabx_xlsx
from .schemas import FIELDS, EDITORIAL_FIELDS, document_for, normalize_record
from .power_text import reconcile_description


ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = {"Card": "cards", "Relic": "relics", "Potion": "potions", "Power": "powers", "Monster": "monsters", "Event": "events", "Keyword": "keywords"}


def baseline_path(page):
    settings = load_config()
    directory = settings.baseline_dir
    name = page.removeprefix("Data:")
    category = next((key for key, value in settings.values["wiki"]["tables"].items() if value == page), None)
    category = category or CATEGORIES.get(name.removesuffix(".tabx").removesuffix("_BETA"))
    reference = settings.baseline_page_for(category).removeprefix("Data:") if category else name
    def candidates(base):
        return [directory / ("Data_" + base + ".xlsx"), directory / (base + ".xlsx"),
                directory / (base + ".json"), directory / ("Data_" + base + ".json"),
                directory / (base.replace(".tabx", "") + ".json")]
    choices = candidates(reference)
    if reference != name:
        choices.extend(candidates(name))
    if category:
        versions = [p for p in directory.glob(category + "_*.json") if not p.name.endswith(".report.json")]
        choices.extend(sorted(versions, key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.stem)), reverse=True))
    return next((p for p in choices if p.is_file()), None)


def load_tabx(page):
    path = baseline_path(page)
    if path:
        if path.suffix.lower() == ".xlsx":
            return read_tabx_xlsx(path)
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        document["_baseline_source"] = str(path.resolve())
        return document
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
    settings = load_config()
    directory = settings.baseline_dir
    names = set()
    file = directory / "page_names.json"
    if file.is_file():
        names.update(json.loads(file.read_text(encoding="utf-8-sig")))
    for page in settings.values["wiki"]["tables"].values():
        for row in records(load_tabx(page)).values():
            if row.get("page"):
                names.add(row["page"])
    return names


def wiki_page(category):
    return load_config().page_for(category)


def write_tabx(filename, content, sort_key=None):
    """Keep the original schema, retain baseline rows/values on extraction gaps.

    Incomplete new records are withheld. A table without the baseline needed to
    preserve editorial fields is explicitly marked as a candidate in a sidecar.
    """
    document = json.loads(content) if isinstance(content, str) else content
    fields = [f["name"] for f in document["schema"]["fields"]]
    category = Path(filename).name.split("_", 1)[0]
    category = "keywords" if category == "keyword" else category
    settings = load_config()
    page = settings.page_for(category)
    baseline_document = load_tabx(page)
    baseline = records(baseline_document)
    destination = Path(filename).resolve()
    history_file = destination.parent / "history" / (category + ".json") if category in ("cards", "relics") else None
    historical = {}
    prior_exports = [p for p in destination.parent.glob(category + "_*.json")
                     if not p.name.endswith(".report.json") and p != destination]
    prior_exports.sort(key=lambda p: tuple(int(n) for n in re.findall(r"\d+", p.stem)))
    for path in (history_file, *prior_exports, destination) if history_file else ():
        if path.is_file():
            historical.update(records(json.loads(path.read_text(encoding="utf-8-sig"))))
    retained = {**historical, **baseline}
    dataset = Dataset(settings.dataset_file)
    source_rows = dataset.items["cards"] if category == "cards" else dataset.get(category)
    source = {}
    for row in source_rows:
        key = row["id"].lower() + ("_upgrade" if row.get("upgrades") == 1 else "")
        source[key] = row
    merged, withheld, preserved, description_choices, metadata_fallbacks, variant_aliases = {}, [], [], [], [], []
    power_aliases = {r["id"].lower(): r.get("_wiki_page", r.get("name")) for r in dataset.document["routes"].get("powers", [])}
    def power_tags(value):
        return {power_aliases.get(name.lower(), name) for name in re.findall(r'\{\{状态\|([^}|]+)', value or "")}
    for row in document["data"]:
        record = dict(zip(fields, row))
        key = record["id"]
        game = source.get(key, {})
        if key not in retained and game.get("_variant_of"):
            # Preserve an existing Wiki ID for a uniquely named instance form.
            aliases = [old_id for old_id, old in retained.items() if old.get("name") == record.get("name")]
            if len(aliases) == 1:
                variant_aliases.append({"generated_id": key, "wiki_id": aliases[0]})
                key = aliases[0]
                record["id"] = key
                source[key] = game
        errors = source.get(key, {}).get("_errors", {})
        # These errors invalidate the rendered row even when a scalar field
        # happened to have a default value in the legacy formatter.
        relevant = set(errors) & (set(fields) | {"constructor", "upgrade", "keywords"})
        if category == "cards" and "multiplayer_constraint" in errors:
            relevant.add("multiplayer_only")
        if category == "monsters":
            relevant.update(set(errors) & {"min_hp", "max_hp", "min_hp_ascension", "max_hp_ascension"})
        if category == "potions" and "pool" in errors:
            relevant.add("pool")
        if relevant:
            withheld.append({"id": key, "fields": sorted(relevant), "errors": errors})
            if key in retained:
                merged[key] = retained[key]
                preserved.append(key)
            continue
        if key in baseline:
            old = baseline[key]
            for field in EDITORIAL_FIELDS.get(category, ("page",)):
                # Null, "None" and empty strings can be intentional Wiki values.
                if field in old:
                    record[field] = old[field]
            if game.get("_variant_of") and old.get("image"):
                record["image"] = old["image"]
            if category == "monsters":
                for field in ("tier", "power", "stage"):
                    if source.get(key, {}).get(field) is not None:
                        record[field] = source[key][field]
                    elif field in old:
                        record[field] = old[field]
                if old.get("tier") in ("爪牙", "衍生"):
                    # Encounter grade describes the fight, not whether a unit
                    # was summoned. The formal table carries that distinction.
                    record["tier"] = old["tier"]
                missing_effects = power_tags(old.get("power")) - power_tags(record.get("power"))
                if missing_effects:
                    record["power"] = old["power"]
                    metadata_fallbacks.append({"id": key, "field": "power", "reason": "self_hook_does_not_cover_existing_spawn_effects", "retained_effects": sorted(missing_effects)})
                if old.get("power") and record.get("power"):
                    # A formal table may deliberately use an ID to disambiguate
                    # duplicate names. Keep that token even when the source's
                    # display title resolves to the same text as another power.
                    preferred = {power_aliases.get(token.lower(), token): token
                                 for token in re.findall(r'\{\{状态\|([^}|]+)', old["power"])
                                 if token.lower() in power_aliases}
                    record["power"] = re.sub(r'(\{\{状态\|)([^}|]+)',
                                              lambda m: m[1] + preferred.get(m[2], m[2]), record["power"])
        if category in ("cards", "relics") and key not in baseline:
            record["predecessor"] = re.sub(r"\+\d*$", "", record.get("name", ""))
        if category == "powers":
            description, choice = reconcile_description(source.get(key, {}), baseline.get(key, {}), record.get("description"))
            if description is not None: record["description"] = description
            if choice: description_choices.append({"id": key, **choice})
        merged[key] = record
    # Missing models can be a decompilation gap rather than a game removal.
    for key, row in retained.items():
        if key not in merged:
            merged[key] = row
            preserved.append(key)
    target_fields = FIELDS.get(category, fields)
    normalized = {}
    for key, row in merged.items():
        normalized[key] = normalize_record(category, row)
    ordered = list(normalized.values())
    if sort_key is not None:
        ordered.sort(key=lambda row: sort_key(row, normalized))
    if "compendium_order" in target_fields:
        for position, row in enumerate(ordered, 1):
            row["compendium_order"] = str(position)
    rows = [[r.get(f) for f in target_fields] for r in ordered]
    if category in FIELDS:
        document = document_for(category, rows, dataset.version)
    else:
        document["data"] = rows
    write_json(filename, document)
    if history_file is not None: write_json(history_file, document)
    missing_editorial_baseline = category in EDITORIAL_FIELDS and baseline_document is None
    partial_baseline = bool(baseline_document and baseline_document.get("_partial"))
    status = {"category": category, "rows": len(document["data"]), "withheld": withheld,
              "preserved_baseline_ids": sorted(set(preserved) & baseline.keys()),
              "preserved_historical_ids": sorted(set(preserved) & historical.keys()),
              "historical_rows": len(historical),
              "history_archive": str(history_file) if history_file is not None else None,
              "source_rows": len(source_rows),
              "instance_variant_ids": sorted(r["id"].lower() for r in source_rows if r.get("_variant_of")),
              "variant_id_aliases": variant_aliases,
              "editorial_baseline_missing": missing_editorial_baseline,
              "baseline_partial": partial_baseline,
              "baseline_source": baseline_document.get("_baseline_source") if baseline_document else None,
              "description_rendering": description_choices,
              "metadata_fallbacks": metadata_fallbacks,
              "wiki_page": page,
              "ready_to_publish": not withheld and not missing_editorial_baseline and not partial_baseline}
    write_json(str(filename) + ".report.json", status)
    if withheld or missing_editorial_baseline or partial_baseline:
        baseline_note = "；人工基线为片段" if partial_baseline else "；缺少人工基线" if missing_editorial_baseline else ""
        print(f"{filename}: 候选数据，缺口 {len(withheld)} 条；保留旧条目 {len(set(preserved))} 条{baseline_note}。详见同名 report.json。")
    return json.dumps(document, ensure_ascii=False, indent=2)


def export_all(dataset_path, output, baseline=None, config_path=None):
    dataset_path = Path(dataset_path).resolve()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    old_cwd, old_path = Path.cwd(), list(sys.path)
    keys = ("STS2_DATASET", "STS2_OFFLINE", "STS2_WIKI_BASELINE", "STS2_CONFIG")
    old_env = {k: os.environ.get(k) for k in keys}
    old_utils = sys.modules.get("utils")
    try:
        os.environ["STS2_DATASET"] = str(Path(dataset_path).resolve())
        os.environ["STS2_OFFLINE"] = "1"
        if config_path:
            os.environ["STS2_CONFIG"] = str(Path(config_path).resolve())
        if baseline:
            os.environ["STS2_WIKI_BASELINE"] = str(Path(baseline).resolve())
        os.chdir(output)
        # Refresh a previously imported compatibility module when invoked twice.
        sys.path.insert(0, str(ROOT))
        sys.modules.pop("utils", None)
        for category in ("card", "relic", "potion", "power", "monster", "event", "keyword"):
            runpy.run_path(str(ROOT / category / ("update_" + category + ".py")), run_name="__main__")
        from .comparison import write_comparison
        write_comparison(dataset_path, output)
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


def create_wiki_site():
    """Resolve credentials only for an explicitly requested Wiki operation."""
    import mwclient
    settings = load_config()
    if settings.offline:
        raise RuntimeError("离线配置禁止访问 Wiki；请先将 wiki.offline 或 STS2_OFFLINE 设为 false。")
    credentials = settings.credentials()
    if not credentials["username"] or not credentials["password"]:
        raise ValueError("请设置 wiki.username_env 和 wiki.password_env 指定的环境变量后连接 Wiki。")
    key = credentials["authkey"]
    site = mwclient.Site(settings.wiki_host, custom_headers={"X-authkey": key} if key else {})
    site.login(username=credentials["username"], password=credentials["password"])
    return site


def snapshot_wiki(output=None):
    """Explicit online read step; normal extraction/export never invokes it."""
    settings = load_config()
    output = Path(output) if output is not None else settings.baseline_dir
    site = create_wiki_site()
    for page in dict.fromkeys(settings.values["wiki"]["baseline_tables"].values()):
        document = json.loads(site.pages[page].text())
        records(document)  # Validate before replacing the local cache.
        write_json(output / (page.removeprefix("Data:") + ".json"), document)
    write_json(Path(output) / "page_names.json", [p.name for p in site.allpages()])
