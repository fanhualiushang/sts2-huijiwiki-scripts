import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_game_data.config import load_config
from local_game_data.wiki import export_all, write_json, snapshot_wiki


ROOT = Path(__file__).resolve().parents[1]


def test_config_paths_and_env_overrides(tmp_path, monkeypatch):
    path = tmp_path / "settings/config.json"
    write_json(path, {"game": {"source_dir": "../recovered", "version": "1.0"}, "paths": {"output_dir": "outputs"}})
    monkeypatch.chdir(tmp_path)
    settings = load_config(path)
    assert settings.source_dir == tmp_path / "recovered"
    assert settings.output_dir == path.parent / "outputs"
    assert settings.dataset_file == path.parent / "outputs/dataset.json"
    assert settings.baseline_dir == path.parent / "outputs/baseline"
    assert settings.page_for("cards") == "Data:Card_BETA.tabx"
    monkeypatch.setenv("STS2_OUTPUT", "environment-output")
    monkeypatch.setenv("STS2_VERSION", "2.0")
    monkeypatch.setenv("STS2_OFFLINE", "true")
    assert load_config(path).output_dir == path.parent / "environment-output"
    assert load_config(path).version == "2.0"
    assert load_config(path).offline is True
    monkeypatch.setenv("STS2_OFFLINE", "false")
    assert load_config(path).offline is False
    monkeypatch.setenv("STS2_CONFIG", str(path))
    assert load_config().path == path


def test_credentials_remain_environment_references(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    write_json(path, {"wiki": {"username_env": "TEST_WIKI_USER", "password_env": "TEST_WIKI_PASSWORD", "authkey_env": "TEST_WIKI_KEY"}})
    monkeypatch.setenv("TEST_WIKI_USER", "test-account")
    monkeypatch.setenv("TEST_WIKI_PASSWORD", "example-password-value")
    monkeypatch.setenv("TEST_WIKI_KEY", "example-authkey-value")
    settings = load_config(path)
    assert "example-password-value" not in repr(settings)
    assert "example-authkey-value" not in repr(settings)
    assert settings.credentials() == {"username": "test-account", "password": "example-password-value", "authkey": "example-authkey-value"}
    monkeypatch.setenv("TEST_WIKI_PASSWORD", "new-example-password")
    assert settings.credentials()["password"] == "new-example-password"
    monkeypatch.delenv("TEST_WIKI_PASSWORD")
    assert settings.credentials()["password"] is None


def test_plaintext_credentials_have_a_value_free_migration_error(tmp_path):
    path = tmp_path / "config.json"
    for document in ({"huijiwiki": {"password": "example-private-value"}}, {"wiki": {"password": "example-private-value"}}):
        write_json(path, document)
        with pytest.raises(ValueError) as error:
            load_config(path)
        assert "example-private-value" not in str(error.value)
        assert "环境变量" in str(error.value)


def test_cli_arguments_override_env_and_selected_config(tmp_path, monkeypatch):
    config = tmp_path / "chosen.json"
    ignored = tmp_path / "ignored.json"
    write_json(config, {"game": {"version": "config-version", "source_dir": "config-source"}})
    write_json(ignored, {"game": {"version": "wrong-version"}})
    monkeypatch.setenv("STS2_CONFIG", str(ignored))
    monkeypatch.setenv("STS2_VERSION", "env-version")
    cli = importlib.import_module("local_game_data.__main__")
    received = []
    monkeypatch.setattr(cli, "extract", lambda *a, **kw: received.append((a, kw)) or {"routes": {}, "issues": []})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["local_game_data", "extract", "--config", str(config), "--version", "cli-version", "--source", "cli-source", "--output", "cli-output"])
    cli.main()
    assert received[0][0] == (tmp_path / "cli-source", "cli-version", tmp_path / "cli-output", "zhs")
    assert received[0][1]["dataset_file"] == tmp_path / "cli-output/dataset.json"
    monkeypatch.setattr("sys.argv", ["local_game_data", "extract", "--config", str(config)])
    cli.main()
    assert received[1][0][0] == tmp_path / "config-source"
    assert received[1][0][1] == "env-version"


def test_cli_respects_custom_dataset_destination(tmp_path, monkeypatch):
    from local_game_data import dataset
    path = tmp_path / "config.json"
    write_json(path, {"game": {"version": "test-version"}, "paths": {"output_dir": "output", "dataset_file": "snapshots/chosen.json"}})
    class FakeExtractor:
        def __init__(self, source, version, lang):
            self.version = version
        def build(self):
            return {"schema_version": 1, "version": self.version, "source": "test-source", "input_hash": "test-hash",
                    "model_counts": {}, "placeholders": [], "issues": [], "routes": {}, "items": {}}
    monkeypatch.setattr(dataset, "Extractor", FakeExtractor)
    monkeypatch.setattr("sys.argv", ["local_game_data", "extract", "--config", str(path)])
    importlib.import_module("local_game_data.__main__").main()
    assert json.loads((tmp_path / "snapshots/chosen.json").read_text(encoding="utf-8"))["version"] == "test-version"
    assert (tmp_path / "output/report.json").is_file()
    assert not (tmp_path / "output/dataset.json").exists()


def test_snapshot_uses_configured_pages_and_credentials(tmp_path, monkeypatch):
    fixtures = json.loads((ROOT / "tests/fixtures/wiki_beta_tables.json").read_text(encoding="utf-8"))
    path = tmp_path / "config.json"
    write_json(path, {"wiki": {"host": "wiki.example.test", "username_env": "SNAPSHOT_USER", "password_env": "SNAPSHOT_PASSWORD", "authkey_env": "SNAPSHOT_KEY",
                               "tables": {"cards": "Data:CustomCard_BETA.tabx"}}})
    monkeypatch.setenv("STS2_CONFIG", str(path))
    monkeypatch.setenv("SNAPSHOT_USER", "snapshot-user")
    monkeypatch.setenv("SNAPSHOT_PASSWORD", "snapshot-password")
    monkeypatch.setenv("SNAPSHOT_KEY", "snapshot-key")
    pages, calls = {}, []
    for name in load_config().values["wiki"]["tables"].values():
        sample = fixtures.get(name.removeprefix("Data:").removesuffix(".tabx")) or fixtures["Card_BETA"]
        pages[name] = SimpleNamespace(text=lambda sample=sample: json.dumps(sample))
    class FakeSite:
        def __init__(self, host, custom_headers):
            calls.append((host, custom_headers))
            self.pages = pages
        def login(self, **credentials):
            calls.append(credentials)
        def allpages(self):
            return [SimpleNamespace(name="打击"), SimpleNamespace(name="力量")]
    import mwclient
    monkeypatch.setattr(mwclient, "Site", FakeSite)
    snapshot_wiki(tmp_path / "baseline")
    assert calls == [("wiki.example.test", {"X-authkey": "snapshot-key"}), {"username": "snapshot-user", "password": "snapshot-password"}]
    assert (tmp_path / "baseline/CustomCard_BETA.tabx.json").is_file()
    assert (tmp_path / "baseline/Monster_BETA.tabx.json").is_file()
    assert not (tmp_path / "baseline/Card.tabx.json").exists()
    assert json.loads((tmp_path / "baseline/page_names.json").read_text(encoding="utf-8")) == ["打击", "力量"]


def test_beta_schema_and_editorial_fields_survive_export(extracted, tmp_path):
    fixtures = json.loads((ROOT / "tests/fixtures/wiki_beta_tables.json").read_text(encoding="utf-8"))
    baseline = tmp_path / "baseline"
    for page, document in fixtures.items():
        write_json(baseline / (page + ".tabx.json"), {**document, "_partial": True})
    # An explicit old null must stay null; existing Wiki image filenames are
    # editorial references and must not be regenerated from a game path.
    cards = fixtures["Card_BETA"]
    names = [f["name"] for f in cards["schema"]["fields"]]
    cards["data"][0][names.index("beta_image")] = "custom_beta_portrait.png"
    write_json(baseline / "Card_BETA.tabx.json", {**cards, "_partial": True})
    export_all(extracted / "dataset.json", tmp_path / "out", baseline)
    for category, page in (("cards", "Card_BETA"), ("relics", "Relic_BETA"), ("powers", "Power_BETA"), ("monsters", "Monster_BETA")):
        target = tmp_path / "out" / f"{category}_0.113.1.json"
        doc = json.loads(target.read_text(encoding="utf-8"))
        assert doc["schema"] == fixtures[page]["schema"]
        fields = [f["name"] for f in doc["schema"]["fields"]]
        rows = {r[fields.index("id")]: dict(zip(fields, r)) for r in doc["data"]}
        if category == "cards":
            assert rows["strike_ironclad"]["predecessor"] == "打击（红）"
            assert rows["strike_ironclad_upgrade"]["predecessor"] == "打击（红）"
            assert rows["strike_ironclad"]["beta_image"] == "custom_beta_portrait.png"
            assert rows["strike_ironclad"]["multiplayer_only"] == "否"
        elif category == "relics":
            assert rows["burning_blood"]["predecessor"] == "燃烧之血"
            assert rows["divine_right"]["predecessor"] is None
            assert rows["burning_blood"]["ancient"] is None
        elif category == "powers":
            assert rows["strength"]["allow_negative"] == "True"
            assert rows["accelerant"]["allow_negative"] == "False"
            assert all(r["category"] == "power" for r in rows.values())
        else:
            assert rows["aeonglass"]["icon"] == "aeonglass_bossicon.png"
            assert rows["architect"]["icon"] is None
            assert rows["assassin_ruby_raider"]["power"] is None
            assert rows["aeonglass"]["stage"] == "荣耀"
        report = json.loads(Path(str(target) + ".report.json").read_text(encoding="utf-8"))
        assert report["wiki_page"] == f"Data:{page}.tabx"
        assert report["baseline_partial"] is True
        assert report["ready_to_publish"] is False
        assert "_partial" not in doc
