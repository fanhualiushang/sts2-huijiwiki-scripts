import json

import pytest

from local_game_data.power_text import normalize_temporary_stat_description
from local_game_data.dataset import write_json
from local_game_data.wiki import records, write_tabx


@pytest.mark.parametrize(("action", "stat"), [("获得", "集中"), ("获得", "敏捷"), ("获得", "力量"), ("失去", "力量"), ("失去", "集中")])
def test_all_five_temporary_stat_rules_are_idempotent(action, stat):
    before = "在本回合结束前" + action + "{{颜色|gold|" + stat + "}}。"
    after = "在本回合结束前" + action + "{{颜色|blue|X}}点{{颜色|gold|" + stat + "}}。"
    assert normalize_temporary_stat_description(before) == after
    assert normalize_temporary_stat_description(after) == after


@pytest.mark.parametrize("text", [
    "在本回合结束前获得{{颜色|blue|2}}点{{颜色|gold|力量}}。",
    "在下回合结束前获得{{颜色|gold|敏捷}}。",
    "获得{{颜色|gold|集中}}。",
    "在本回合获得{{颜色|gold|荆棘}}。",
    "在本回合结束前失去{{颜色|gold|敏捷}}。",
    None,
])
def test_other_descriptions_and_existing_numbers_are_unchanged(text):
    assert normalize_temporary_stat_description(text) == text


def test_final_rule_also_applies_to_formal_and_retained_rows(tmp_path, monkeypatch):
    fields = ["id", "name", "description"]
    schema = {"fields": [{"name": field} for field in fields]}
    text = "在本回合结束前获得{{颜色|gold|敏捷}}。"
    write_json(tmp_path / "baseline/Power.tabx.json", {"schema": schema, "data": [["retained", "旧状态", text], ["fallback", "回退状态", text]]})
    write_json(tmp_path / "dataset.json", {"schema_version": 1, "version": "test", "routes": {"powers": [{"id": "FALLBACK", "_errors": {"description": "Needs runtime context"}}, {"id": "NEW"}]}, "items": {}})
    monkeypatch.setenv("STS2_DATASET", str(tmp_path / "dataset.json"))
    monkeypatch.setenv("STS2_WIKI_BASELINE", str(tmp_path / "baseline"))
    destination = tmp_path / "powers_test.json"
    write_tabx(destination, {"schema": schema, "data": [["fallback", "回退状态", ""], ["new", "新状态", text]]})
    actual = records(json.loads(destination.read_text(encoding="utf-8")))
    assert set(actual) == {"retained", "fallback", "new"}
    assert all(r["description"] == "在本回合结束前获得{{颜色|blue|X}}点{{颜色|gold|敏捷}}。" for r in actual.values())
