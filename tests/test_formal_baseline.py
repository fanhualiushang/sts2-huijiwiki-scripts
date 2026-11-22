from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from local_game_data.baseline import read_tabx_xlsx
from local_game_data.wiki import baseline_path, export_all, load_tabx, records, write_json
from local_game_data.power_text import reconcile_description


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "local_data/baseline"


def test_xlsx_sparse_cells_and_null_strings(monkeypatch):
    # Mock the read-only ZIP boundary, rather than creating/modifying a workbook.
    import local_game_data.baseline as module
    files = {
        "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="data" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>id</t></si><si><t>predecessor</t></si><si><t>beta_image</t></si><si><t>None</t></si><si><t>example</t></si></sst>',
        "xl/worksheets/sheet1.xml": '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row><row r="2"><c r="A2" t="s"><v>4</v></c><c r="C2" t="s"><v>3</v></c></row></sheetData></worksheet>',
    }
    class Archive:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def namelist(self): return list(files)
        def read(self, name): return files[name].encode()
    monkeypatch.setattr(module, "ZipFile", lambda path: Archive())
    document = read_tabx_xlsx("readonly.xlsx")
    assert document["data"] == [["example", None, "None"]]
    assert [f["name"] for f in document["schema"]["fields"]] == ["id", "predecessor", "beta_image"]


def test_symbolic_amounts_and_concrete_application():
    record = {"description_template": "获得[blue]{Amount}[/blue]点格挡。", "smart_description_template": "获得[blue]{Amount}[/blue]点格挡。", "description_variables": {}}
    value, report = reconcile_description(record, {"description": "获得{{颜色|blue|X}}点格挡。"}, "获得1点格挡。")
    assert value == "获得{{颜色|blue|X}}点格挡。"
    record["default_application_amount"] = 2
    value, report = reconcile_description(record, {"description": "获得2点格挡。"}, "获得1点格挡。")
    assert value == "获得{{颜色|blue|2}}点格挡。"
    assert report["mode"] == "source_application_amount"
    del record["default_application_amount"]
    value, report = reconcile_description(record, {"description": "获得3点格挡。"}, "获得1点格挡。")
    assert value == "获得3点格挡。"
    assert report["mode"] == "formal_application_amount_fallback"


@pytest.fixture(scope="module")
def formal_export(extracted, tmp_path_factory):
    paths = [FORMAL / f"Data_{name}.tabx.xlsx" for name in ("Card", "Relic", "Power", "Monster")]
    if not all(p.is_file() for p in paths):
        pytest.skip("Formal Wiki downloads are private local test inputs")
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    output = tmp_path_factory.mktemp("formal-exports")
    export_all(extracted / "dataset.json", output, FORMAL)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in hashes.items())
    return output


def table(path):
    return records(json.loads(path.read_text(encoding="utf-8")))


def test_formal_card_and_relic_columns_are_copied(formal_export):
    for category, name, fields in (("cards", "Card", ("predecessor", "beta_image")), ("relics", "Relic", ("predecessor",))):
        baseline = records(read_tabx_xlsx(FORMAL / f"Data_{name}.tabx.xlsx"))
        generated = table(formal_export / f"{category}_0.113.1.json")
        assert baseline.keys() <= generated.keys()
        for key, row in baseline.items():
            for field in fields: assert generated[key][field] == row.get(field)
        for key in generated.keys() - baseline.keys():
            assert generated[key]["predecessor"] == generated[key]["name"].split("+", 1)[0]
    relics = table(formal_export / "relics_0.113.1.json")
    for key, pool in (("black_blood", "铁甲战士"), ("ring_of_the_drake", "静默猎手"), ("divine_destiny", "储君"), ("phylactery_unbound", "亡灵契约师"), ("infused_core", "故障机器人"), ("storyteller", "愚者")):
        assert relics[key]["pool"] == pool


def test_formal_power_conventions_and_runtime_text(formal_export):
    baseline = records(read_tabx_xlsx(FORMAL / "Data_Power.tabx.xlsx"))
    generated = table(formal_export / "powers_0.113.1.json")
    for key, row in baseline.items():
        assert generated[key]["allow_negative"] == row["allow_negative"]
        assert generated[key]["page"] == row["page"]
    assert Counter(r["allow_negative"] for r in generated.values())["True"] == 5
    assert "[E]" not in "".join(r["description"] for r in generated.values())
    assert '[[File:colorless_energy_icon.png|16px|link=能量]]' in generated["free_attack"]["description"]
    assert "{{颜色|blue|X}}点" in generated["strength"]["description"]
    assert "{{颜色|blue|2}}点" in generated["pillar_of_creation"]["description"]
    assert "{{颜色|blue|3}}张" in generated["chains_of_binding"]["description"]
    for key in ("inferno", "crimson_mantle"):
        assert "失去生命" in generated[key]["description"]
        assert "失去{{颜色|blue|0}}" not in generated[key]["description"]
    for key, page in (("anticipate", "临时敏捷"), ("coordinate", "临时力量"), ("crush_under", "临时失去力量"), ("focused_strike", "临时集中")):
        assert generated[key]["page"] == page
    assert "{{颜色|blue|3}}" in generated["the_bomb"]["description"]


def test_monster_metadata_does_not_drop_spawn_buffs(formal_export):
    baseline = records(read_tabx_xlsx(FORMAL / "Data_Monster.tabx.xlsx"))
    generated = table(formal_export / "monsters_0.113.1.json")
    for key, row in baseline.items():
        for field in ("tier", "power", "stage"):
            if row.get(field): assert generated[key].get(field)
    assert generated["eye_with_teeth"]["power"] == baseline["eye_with_teeth"]["power"]
    assert generated["zapbot"]["power"] == baseline["zapbot"]["power"]
    assert generated["parafright"]["tier"] == "爪牙"
    assert generated["fat_gremlin"]["tier"] == "衍生"
    assert generated["aeonglass"]["power"] == baseline["aeonglass"]["power"]
    assert generated["axebot"]["power"] == "{{状态|库存}}2"
    assert generated["corpse_slug"]["power"] == "{{状态|ravenous}}4 （高进阶5）"
    comparison = json.loads((formal_export / "baseline_comparison.json").read_text(encoding="utf-8"))
    assert comparison["tables"]["cards"]["field_difference_counts"]["predecessor"] == 0
    assert comparison["tables"]["cards"]["field_difference_counts"]["beta_image"] == 0
    assert comparison["tables"]["powers"]["unconverted_energy_token_ids"] == []


def test_formal_xlsx_has_priority_over_old_beta_fragment(monkeypatch):
    if not (FORMAL / "Data_Card.tabx.xlsx").is_file(): pytest.skip("Local formal download not present")
    monkeypatch.setenv("STS2_WIKI_BASELINE", str(FORMAL))
    assert baseline_path("Data:Card_BETA.tabx").name == "Data_Card.tabx.xlsx"
    assert len(records(load_tabx("Data:Card_BETA.tabx"))) == 1177
