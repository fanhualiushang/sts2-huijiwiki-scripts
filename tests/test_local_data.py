"""Verify real game examples and the original writers' public output contract."""
import json
from pathlib import Path
import socket
import sys
import importlib
from types import SimpleNamespace

import pytest

from local_game_data import Dataset
from local_game_data.csharp import ExtractionError
from local_game_data.formatting import render
from local_game_data.wiki import export_all, write_tabx, write_json, load_tabx


ROOT = Path(__file__).resolve().parents[1]


def test_nested_formats_and_unsupported_variables():
    assert render("{InCombat:{Damage:diff()}|通用说明}", {"InCombat": False}) == "通用说明"
    assert render("{IfUpgraded:show:指定|随机}", {"IfUpgraded": True}) == "指定"
    assert render("{Amount:cond:<0?失去|获得}{Amount:abs()}", {"Amount": -3}) == "失去3"
    assert render("{Energy:energyIcons()}", {"Energy": 2}) == "[E][E]"
    with pytest.raises(ExtractionError, match="Missing localization variable"):
        render("造成{Missing:diff()}点伤害", {})


def test_real_card_upgrades(extracted):
    d = Dataset(extracted / "dataset.json")
    cards = {(r["id"], r["upgrades"]): r for r in d.items["cards"]}
    assert cards["STRIKE_IRONCLAD", 0]["description"] == "造成6点伤害。"
    assert cards["STRIKE_IRONCLAD", 1]["description"] == "造成9点伤害。"
    assert "5点" in cards["DEFEND_IRONCLAD", 0]["description"]
    assert "8点" in cards["DEFEND_IRONCLAD", 1]["description"]
    assert cards["WHIRLWIND", 0]["cost"] == "X"
    assert cards["BODY_SLAM", 0]["cost"] == "1"
    assert cards["BODY_SLAM", 1]["cost"] == "0"
    assert "随机" in cards["TRUE_GRIT", 0]["description"]
    assert "随机" not in cards["TRUE_GRIT", 1]["description"]
    assert "[gold]固有[/gold]" in cards["AGGRESSION", 1]["description"]
    assert "8点" in cards["WHIRLWIND", 1]["description"]
    assert ("ASCENDERS_BANE", 1) not in cards
    # Multi-upgrade card uses the game's increment-before-OnUpgrade rule.
    assert "11点" in cards["DINGY_DAGGER", 1]["description"]
    assert cards["DINGY_DAGGER", 1]["name"].endswith("+1")
    assert not [r for r in cards.values() if r.get("_errors")]
    copy = d.get("cards")
    copy[0]["name"] = "changed"
    assert d.get("cards")[0]["name"] != "changed"


def test_other_categories_and_inventory(extracted):
    d = Dataset(extracted / "dataset.json")
    routes = {k: {r["id"]: r for r in d.get(k)} for k in ("relics", "potions", "powers", "monsters", "events")}
    assert "8[/blue]" in routes["relics"]["AKABEKO"]["description"]
    assert "20" in routes["potions"]["FIRE_POTION"]["description"]
    assert routes["powers"]["STRENGTH"]["allow_negative"] is True
    assert routes["powers"]["STRENGTH"]["stack_type"] == "Counter"
    assert routes["monsters"]["TEST_SUBJECT"]["min_hp"] == "100 / 200 / 300"
    assert routes["monsters"]["TEST_SUBJECT"]["min_hp_ascension"] == "111 / 212 / 313"
    assert "ARCANE_SCROLL" in routes["events"]["NEOW"]["relics"]
    assert routes["potions"]["BOTTLED_SONG"]["pool"] == "fool"
    for category, total in d.document["model_counts"].items():
        assert len(d.get(category)) == total
    for rows in d.document["routes"].values():
        assert len({r["id"] for r in rows}) == len(rows)
        assert all(".Mocks" not in r.get("_source", "") for r in rows)


def test_all_original_writers_offline_and_schema(extracted, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline generation attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    import mwclient
    monkeypatch.setattr(mwclient, "Site", forbidden)
    export_all(extracted / "dataset.json", tmp_path)
    contract = json.loads((ROOT / "tests/fixtures/original_fields.json").read_text(encoding="utf-8"))
    beta = json.loads((ROOT / "tests/fixtures/wiki_beta_tables.json").read_text(encoding="utf-8"))
    for category, fields in contract.items():
        filename = ("keyword" if category == "keyword" else category + "s") + "_0.113.1.json"
        doc = json.loads((tmp_path / filename).read_text(encoding="utf-8"))
        page = category.title() + "_BETA"
        if page in beta:
            assert doc["schema"] == beta[page]["schema"]
            fields = [f["name"] for f in beta[page]["schema"]["fields"]]
            assert doc.get("description") == beta[page].get("description")
            if category == "power": assert doc["sources"] == "导出数据自版本 0.113.1"
            else: assert "sources" not in doc
        else:
            assert [f["name"] for f in doc["schema"]["fields"]] == fields
            assert all(f == {"name": f["name"], "type": "string", "title": {"en": f["name"], "zh": ""}} for f in doc["schema"]["fields"])
            assert doc["sources"] == "导出数据自版本 0.113.1"
        assert doc["data"] and all(len(row) == len(fields) for row in doc["data"])
        ids = [row[fields.index("id")] for row in doc["data"]]
        assert len(ids) == len(set(ids))
        if category == "card":
            by_id = {r[fields.index("id")]: r for r in doc["data"]}
            assert by_id["dingy_dagger_upgrade"][fields.index("page")] == by_id["dingy_dagger"][fields.index("page")]
            assert all(r[fields.index("multiplayer_only")] in ("是", "否") for r in doc["data"])
        if category == "power":
            assert all(r[fields.index("category")] == "power" for r in doc["data"])
            assert all(r[fields.index("allow_negative")] in ("True", "False") for r in doc["data"])
    sys.modules.pop("utils", None)


def test_legacy_wiki_connection_is_lazy(extracted, monkeypatch):
    import mwclient
    calls = []
    class FakeSite:
        def __init__(self, *args, **kwargs):
            calls.append("connect")
            self.pages = {"Data:Example.tabx": SimpleNamespace(text=lambda: json.dumps({"schema": {"fields": [{"name": "id"}, {"name": "note"}]}, "data": [["item", "保留备注"]]}))}
        def login(self, **kwargs):
            calls.append("login")
    monkeypatch.setattr(mwclient, "Site", FakeSite)
    monkeypatch.setenv("STS2_DATASET", str(extracted / "dataset.json"))
    monkeypatch.setenv("HUIJI_WIKI_USERNAME", "test-user")
    monkeypatch.setenv("HUIJI_WIKI_PASSWORD", "test-password")
    monkeypatch.delenv("STS2_OFFLINE", raising=False)
    sys.modules.pop("utils", None)
    module = importlib.import_module("utils")
    assert calls == []
    assert module.parse_tabx("Data:Example.tabx", "id")["item"]["note"] == "保留备注"
    assert calls == ["connect", "login"]
    assert module.site.pages is module.site.pages
    assert calls == ["connect", "login"]
    monkeypatch.setenv("STS2_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="离线"):
        module.site.pages
    sys.modules.pop("utils", None)


def test_incomplete_records_preserve_baseline(tmp_path, monkeypatch):
    dataset = {"schema_version": 1, "version": "test", "routes": {"powers": [
        {"id": "OLD", "_errors": {"description": "unsupported expression"}},
        {"id": "NEW", "_errors": {"description": "unsupported expression"}}]}, "items": {}}
    write_json(tmp_path / "dataset.json", dataset)
    fields = [{"name": x} for x in ("id", "name", "description")]
    baseline = {"schema": {"fields": fields}, "data": [["old", "旧名称", "人工核对的旧说明"], ["missing", "旧条目", "不能静默删除"]]}
    write_json(tmp_path / "baseline/Power_BETA.tabx.json", baseline)
    monkeypatch.setenv("STS2_DATASET", str(tmp_path / "dataset.json"))
    monkeypatch.setenv("STS2_WIKI_BASELINE", str(tmp_path / "baseline"))
    candidate = {"sources": "test", "schema": {"fields": fields}, "data": [["old", "新名称", ""], ["new", "新增", ""]]}
    target = tmp_path / "powers_test.json"
    write_tabx(target, candidate)
    result = json.loads(target.read_text(encoding="utf-8"))
    actual_fields = [f["name"] for f in result["schema"]["fields"]]
    assert [[r[actual_fields.index(f)] for f in ("id", "name", "description")] for r in result["data"]] == baseline["data"]
    assert all(r[actual_fields.index("category")] == "power" for r in result["data"])
    assert not json.loads(Path(str(target) + ".report.json").read_text(encoding="utf-8"))["ready_to_publish"]


def test_monster_editorial_fields_are_preserved(extracted, tmp_path, monkeypatch):
    fields = ["id", "name", "minHP", "tier", "power", "stage", "note", "page"]
    schema = {"fields": [{"name": x} for x in fields]}
    write_json(tmp_path / "baseline/Monster_BETA.tabx.json", {"schema": schema, "data": [["test_subject", "旧名", "旧数值", "Boss", "原有状态列表", "第三幕", "人工备注", "实验体"]]})
    monkeypatch.setenv("STS2_DATASET", str(extracted / "dataset.json"))
    monkeypatch.setenv("STS2_WIKI_BASELINE", str(tmp_path / "baseline"))
    target = tmp_path / "monsters_test.json"
    write_tabx(target, {"schema": schema, "data": [["test_subject", "实验体 #C8", "100 / 200 / 300", "", "", "", "无", "新标题"]]})
    result = json.loads(target.read_text(encoding="utf-8"))
    output_fields = [f["name"] for f in result["schema"]["fields"]]
    row = [result["data"][0][output_fields.index(f)] for f in fields]
    assert row[2] == "100 / 200 / 300"
    assert row[3:] == ["Boss", "原有状态列表", "第三幕", "人工备注", "实验体"]


def test_baseline_selects_version_not_report(tmp_path, monkeypatch):
    monkeypatch.setenv("STS2_WIKI_BASELINE", str(tmp_path))
    write_json(tmp_path / "powers_0.9.0.json", {"version": "old"})
    write_json(tmp_path / "powers_0.113.1.json", {"version": "new"})
    write_json(tmp_path / "powers_0.113.1.json.report.json", {"not_a_table": True})
    assert load_tabx("Data:Power.tabx") == {"version": "new"}
