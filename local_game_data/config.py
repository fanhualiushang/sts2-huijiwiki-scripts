"""One configuration entry point; secrets are resolved only when connecting."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {
    "game": {"source_dir": "../sts2_source_code", "version": None, "language": "zhs"},
    "paths": {"output_dir": "local_data", "dataset_file": None, "baseline_dir": None,
              "card_images_dir": None, "animations_dir": None},
    "wiki": {
        "host": "sts2.huijiwiki.com", "offline": False,
        "username_env": "HUIJI_WIKI_USERNAME", "password_env": "HUIJI_WIKI_PASSWORD",
        "authkey_env": "HUIJI_WIKI_STS2_KEY",
        "tables": {"cards": "Data:Card_BETA.tabx", "relics": "Data:Relic_BETA.tabx",
                   "powers": "Data:Power_BETA.tabx", "monsters": "Data:Monster_BETA.tabx",
                   "potions": "Data:Potion.tabx", "events": "Data:Event.tabx",
                   "keywords": "Data:Keyword.tabx"},
        "baseline_tables": {"cards": "Data:Card.tabx", "relics": "Data:Relic.tabx",
                            "powers": "Data:Power.tabx", "monsters": "Data:Monster.tabx",
                            "potions": "Data:Potion.tabx", "events": "Data:Event.tabx", "keywords": "Data:Keyword.tabx"},
    },
}
ENV_OVERRIDES = {
    "STS2_SOURCE": ("game", "source_dir"), "STS2_VERSION": ("game", "version"),
    "STS2_LANG": ("game", "language"), "STS2_OUTPUT": ("paths", "output_dir"),
    "STS2_DATASET": ("paths", "dataset_file"), "STS2_WIKI_BASELINE": ("paths", "baseline_dir"),
    "STS2_CARD_IMAGES": ("paths", "card_images_dir"), "STS2_ANIMATIONS": ("paths", "animations_dir"),
}


def merge(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def boolean(value, name):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("1", "true", "yes", "0", "false", "no"):
        return value.lower() in ("1", "true", "yes")
    raise ValueError(f"{name} 必须是 true/false 或 1/0")


@dataclass(frozen=True)
class Settings:
    path: Path
    values: dict

    def resolve(self, value):
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.path.parent / path).resolve()

    @property
    def source_dir(self):
        return self.resolve(self.values["game"]["source_dir"])

    @property
    def version(self):
        return self.values["game"]["version"]

    @property
    def language(self):
        return self.values["game"]["language"]

    @property
    def output_dir(self):
        return self.resolve(self.values["paths"]["output_dir"])

    @property
    def dataset_file(self):
        value = self.values["paths"]["dataset_file"]
        return self.resolve(value) if value else self.output_dir / "dataset.json"

    @property
    def baseline_dir(self):
        value = self.values["paths"]["baseline_dir"]
        return self.resolve(value) if value else self.output_dir / "baseline"

    def optional_path(self, name):
        value = self.values["paths"][name]
        return self.resolve(value) if value else None

    @property
    def offline(self):
        return self.values["wiki"]["offline"]

    @property
    def wiki_host(self):
        return self.values["wiki"]["host"]

    def page_for(self, category):
        return self.values["wiki"]["tables"][category]

    def baseline_page_for(self, category):
        return self.values["wiki"]["baseline_tables"][category]

    def credentials(self):
        # No actual credential is stored in Settings or configuration examples.
        wiki = self.values["wiki"]
        return {key: (os.environ.get(wiki[key + "_env"]) or None) if wiki[key + "_env"] else None
                for key in ("username", "password", "authkey")}


def load_config(path=None):
    configured = path or os.environ.get("STS2_CONFIG")
    selected = Path(configured).expanduser().resolve() if configured else ROOT / "config.json"
    if configured and not selected.is_file():
        raise FileNotFoundError(f"配置文件不存在：{selected}")
    if selected.is_file():
        document = json.loads(selected.read_text(encoding="utf-8-sig"))
        if not isinstance(document, dict):
            raise ValueError("config.json 必须是 JSON 对象")
        legacy = document.pop("huijiwiki", {})
        if isinstance(legacy, dict) and any(legacy.get(k) for k in ("username", "password", "X-authkey")):
            raise ValueError("请将旧版 huijiwiki 明文凭据迁移到环境变量，并参考 config.example.json 更新配置。")
        wiki = document.get("wiki", {})
        if isinstance(wiki, dict) and any(k in wiki for k in ("username", "password", "authkey", "X-authkey")):
            raise ValueError("wiki 凭据请使用 username_env/password_env/authkey_env 引用环境变量，不填写明文值。")
        values = merge(DEFAULTS, document)
    else:
        values = deepcopy(DEFAULTS)
    for env_name, (section, key) in ENV_OVERRIDES.items():
        if env_name in os.environ:
            values[section][key] = os.environ[env_name]
    values["wiki"]["offline"] = boolean(os.environ.get("STS2_OFFLINE", values["wiki"]["offline"]), "wiki.offline / STS2_OFFLINE")
    for section in ("tables", "baseline_tables"):
        for category, page in values["wiki"][section].items():
            if not isinstance(page, str) or not page.startswith("Data:") or not page.endswith(".tabx"):
                raise ValueError(f"wiki.{section}.{category} 必须是 Data:名称.tabx")
    return Settings(selected, values)
