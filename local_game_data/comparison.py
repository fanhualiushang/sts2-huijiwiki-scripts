"""Inspect changes against the formal Wiki tables after each export."""
import hashlib
from pathlib import Path

from .config import load_config
from .dataset import Dataset, write_json


COMPARE_FIELDS = {
    "cards": ("predecessor", "beta_image", "name", "description", "description_raw"),
    "relics": ("predecessor", "pool", "name", "description"),
    "powers": ("allow_negative", "page", "name", "description"),
    "monsters": ("tier", "power", "stage", "minHP", "maxHP", "ascenderminHP", "ascendermaxHP"),
}


def write_comparison(dataset_path, output):
    import json
    from .wiki import load_tabx, records
    settings = load_config()
    dataset = Dataset(dataset_path)
    report = {"source_version": dataset.version, "tables": {}}
    for category, fields in COMPARE_FIELDS.items():
        filename = Path(output) / f"{category}_{dataset.version}.json"
        reference = load_tabx(settings.page_for(category))
        if reference is None or not filename.is_file(): continue
        old = records(reference)
        current = records(json.loads(filename.read_text(encoding="utf-8")))
        source_file = reference.get("_baseline_source")
        table = {"reference_file": source_file, "reference_rows": len(old), "output_rows": len(current),
                 "new_ids": sorted(current.keys() - old.keys()), "missing_reference_ids": sorted(old.keys() - current.keys()),
                 "field_difference_counts": {}, "differences": []}
        if source_file and Path(source_file).is_file():
            table["reference_sha256"] = hashlib.sha256(Path(source_file).read_bytes()).hexdigest()
        for field in fields:
            changes = [{"id": key, "name": current[key].get("name"), "field": field,
                        "reference": old[key].get(field), "output": current[key].get(field)}
                       for key in sorted(old.keys() & current.keys()) if old[key].get(field) != current[key].get(field)]
            table["field_difference_counts"][field] = len(changes)
            table["differences"].extend(changes)
        if category == "cards":
            table["formatting_only_description_count"] = sum(
                old[key].get("description") != current[key].get("description") and
                old[key].get("description_raw") == current[key].get("description_raw")
                for key in old.keys() & current.keys())
        if category == "monsters":
            table["unclassified_new_ids"] = [key for key in table["new_ids"]
                                             if not current[key].get("tier") or not current[key].get("stage")]
        if category == "powers":
            table["unconverted_energy_token_ids"] = [key for key, row in current.items() if "[E]" in (row.get("description") or "")]
        report["tables"][category] = table
    write_json(Path(output) / "baseline_comparison.json", report)
    return report
