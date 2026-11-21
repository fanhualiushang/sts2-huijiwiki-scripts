"""Published Huiji BETA table contracts supplied by the wiki maintainer."""
from copy import deepcopy


FIELDS = {
    "cards": ["category", "id", "name", "color", "rarity", "type", "cost", "description",
              "description_raw", "upgrade", "compendium_order", "image", "beta_image", "page",
              "multiplayer_only", "predecessor"],
    "relics": ["category", "id", "name", "pool", "tier", "description", "description_raw",
               "flavor", "ancient", "compendium_order", "image", "page", "predecessor"],
    "powers": ["category", "id", "name", "description", "type", "stack_type", "allow_negative", "image", "page"],
    "monsters": ["category", "id", "name", "image", "minHP", "maxHP", "ascenderminHP", "ascendermaxHP",
                 "tier", "power", "stage", "note", "page", "icon"],
}
CATEGORY_VALUES = {"cards": "card_beta", "relics": "relic_beta", "powers": "power", "monsters": "monster_beta"}
EDITORIAL_FIELDS = {"cards": ("page", "beta_image", "predecessor"),
                    "relics": ("page", "predecessor"), "monsters": ("page", "tier", "power", "stage", "note", "icon")}
DEFAULT_VALUES = {"cards": {"beta_image": "None", "predecessor": None},
                  "relics": {"predecessor": None}, "monsters": {"icon": None}}


def field_definitions(category):
    return [{"name": name, "type": "string", "title": {"en": name, "zh": name if category == "cards" or category == "relics" and name == "predecessor" else ""}}
            for name in FIELDS[category]]


def document_for(category, rows, version):
    result = {"schema": {"fields": field_definitions(category)}, "data": deepcopy(rows)}
    if category in ("relics", "monsters"):
        result = {"description": {"zh": "由Tabx编辑器生成"}, **result}
    elif category == "powers":
        result = {"description": {"zh": ""}, "sources": f"导出数据自版本 {version}", **result}
    return result


def normalize_record(category, record):
    result = {**deepcopy(DEFAULT_VALUES.get(category, {})), **record}
    if category in CATEGORY_VALUES:
        result["category"] = CATEGORY_VALUES[category]
    if category == "cards":
        value = result.get("multiplayer_only")
        if value in (True, "True", "true", "是"):
            result["multiplayer_only"] = "是"
        elif value in (False, "False", "false", "否"):
            result["multiplayer_only"] = "否"
        elif value is None:
            result["multiplayer_only"] = "None"
    if category == "powers" and isinstance(result.get("allow_negative"), bool):
        result["allow_negative"] = str(result["allow_negative"])
    return result
