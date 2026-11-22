import json
from pathlib import Path

import pytest

from local_game_data import Dataset
from local_game_data.dataset import write_json
from local_game_data.wiki import export_all, records, write_tabx
from local_game_data.power_text import reconcile_description, differs_only_in_variable_quantity
from local_game_data import monster_info


ROOT = Path(__file__).resolve().parents[1]


def test_character_glass_variants_are_distinct(extracted):
    dataset = Dataset(extracted / "dataset.json")
    relics = {r["id"]: r for r in dataset.get("relics")}
    expected = {"IRONCLAD": ("恶魔玻璃", "铁甲战士"), "SILENT": ("毒液玻璃", "静默猎手"),
                "REGENT": ("贵族玻璃", "储君"), "NECROBINDER": ("巫妖玻璃", "亡灵契约师"),
                "DEFECT": ("齿轮玻璃", "故障机器人"), "FOOL": ("缠结玻璃", "愚者")}
    assert "SEA_GLASS" in relics
    for character, (name, title) in expected.items():
        row = relics["SEA_GLASS_" + character]
        assert row["name"] == name
        assert title in row["description"]
        assert "其他角色" not in row["description"]
        assert row["image"] == "sea_glass.png"
        assert row["_variant_of"] == "SEA_GLASS"
    assert dataset.document["variant_counts"]["relics"] == 6
    assert len(relics) == dataset.document["model_counts"]["relics"] + 6
    orobas = next(r for r in dataset.get("events") if r["id"] == "OROBAS")
    assert all("SEA_GLASS_" + character in orobas["relics"] for character in expected)


def test_final_merge_resorts_and_reindexes_old_rows(extracted, tmp_path):
    fixture = json.loads((ROOT / "tests/fixtures/wiki_beta_tables.json").read_text(encoding="utf-8"))["Card_BETA"]
    fields = [f["name"] for f in fixture["schema"]["fields"]]
    retired = dict(zip(fields, fixture["data"][0]))
    retired.update(id="retired_historical_card", name="阿历史卡", page="阿历史卡", compendium_order="999999")
    fixture["data"].append([retired.get(f) for f in fields])
    baseline = tmp_path / "baseline"
    write_json(baseline / "Card.tabx.json", fixture)
    output = tmp_path / "out"
    export_all(extracted / "dataset.json", output, baseline)
    for category in ("cards", "relics"):
        document = json.loads((output / f"{category}_0.113.1.json").read_text(encoding="utf-8"))
        names = [f["name"] for f in document["schema"]["fields"]]
        orders = [r[names.index("compendium_order")] for r in document["data"]]
        assert orders == [str(i) for i in range(1, len(orders) + 1)]
    cards = records(json.loads((output / "cards_0.113.1.json").read_text(encoding="utf-8")))
    assert int(cards["retired_historical_card"]["compendium_order"]) < 10
    assert cards["retired_historical_card"]["compendium_order"] != "999999"


def test_deleted_relic_survives_new_version_and_missing_output(tmp_path, monkeypatch):
    fields = ["id", "name", "predecessor", "compendium_order"]
    schema = {"fields": [{"name": f} for f in fields]}
    old = {"schema": schema, "data": [["retired", "历史遗物", "前作链接", "888"]]}
    write_json(tmp_path / "relics_0.9.0.json", old)
    dataset = tmp_path / "dataset.json"
    write_json(dataset, {"schema_version": 1, "version": "new", "routes": {"relics": [{"id": "ACTIVE"}]}, "items": {}})
    monkeypatch.setenv("STS2_DATASET", str(dataset))
    monkeypatch.setenv("STS2_WIKI_BASELINE", str(tmp_path / "empty-baseline"))
    target = tmp_path / "relics_1.0.0.json"
    write_tabx(target, {"schema": schema, "data": [["active", "新遗物", None, "37"]]})
    rows = records(json.loads(target.read_text(encoding="utf-8")))
    assert rows["retired"]["name"] == "历史遗物"
    assert rows["retired"]["predecessor"] == "前作链接"
    # Prove the archive independently retains it, not merely the prior output.
    target.unlink()
    (tmp_path / "relics_0.9.0.json").unlink()
    new_target = tmp_path / "relics_2.0.0.json"
    write_tabx(new_target, {"schema": schema, "data": []})
    assert set(records(json.loads(new_target.read_text(encoding="utf-8")))) == {"active", "retired"}


@pytest.mark.parametrize(("reference", "candidate"), [
    ("在本回合获得{{颜色|blue|X}}点{{颜色|gold|荆棘}}。", "在本回合获得{{颜色|gold|荆棘}}。"),
    ("在本回合结束前获得{{颜色|blue|X}}点{{颜色|gold|敏捷}}。", "在本回合结束前获得{{颜色|gold|敏捷}}。"),
])
def test_only_missing_x_amount_keeps_formal_wording(reference, candidate):
    result, report = reconcile_description({}, {"description": reference}, candidate)
    assert result == reference
    assert report["mode"] == "formal_variable_quantity"
    assert not differs_only_in_variable_quantity(reference, candidate.replace("本回合", "下回合"))


def test_ravenous_id_is_used_even_without_monster_baseline(monkeypatch):
    monkeypatch.setattr(monster_info, "_spawn_powers", lambda *args: [("RavenousPower", 4)])
    text = monster_info.starting_power_text(None, None, {"RavenousPower": {"id": "RAVENOUS", "name": "饥饿", "stack_type": "Counter"}})
    assert text == "{{状态|ravenous}}4"


def test_existing_wiki_id_and_image_for_instance_variant_are_retained(tmp_path, monkeypatch):
    fields = ["id", "name", "image", "page", "predecessor"]
    schema = {"fields": [{"name": f} for f in fields]}
    write_json(tmp_path / "baseline/Relic.tabx.json", {"schema": schema, "data": [["demon_glass_legacy", "恶魔玻璃", "custom.png", "海玻璃", None]]})
    path = tmp_path / "dataset.json"
    write_json(path, {"schema_version": 1, "version": "test", "routes": {"relics": [{"id": "SEA_GLASS_IRONCLAD", "_variant_of": "SEA_GLASS"}]}, "items": {}})
    monkeypatch.setenv("STS2_DATASET", str(path))
    monkeypatch.setenv("STS2_WIKI_BASELINE", str(tmp_path / "baseline"))
    target = tmp_path / "relics_test.json"
    write_tabx(target, {"schema": schema, "data": [["sea_glass_ironclad", "恶魔玻璃", "sea_glass.png", "恶魔玻璃", None]]})
    result = records(json.loads(target.read_text(encoding="utf-8")))
    assert set(result) == {"demon_glass_legacy"}
    assert result["demon_glass_legacy"]["image"] == "custom.png"
    assert result["demon_glass_legacy"]["page"] == "海玻璃"


def test_failed_json_write_cannot_truncate_history(tmp_path):
    path = tmp_path / "history/relics.json"
    write_json(path, {"known": "must survive"})
    previous = path.read_bytes()
    with pytest.raises(TypeError):
        write_json(path, {"unsupported": object()})
    assert path.read_bytes() == previous
    assert list(path.parent.glob("*.tmp")) == []
