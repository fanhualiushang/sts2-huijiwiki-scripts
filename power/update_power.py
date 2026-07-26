import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from utils import *

data = get_data_by_api("powers")
print(f"共找到 {len(data)} 个状态数据，正在处理...")

name_remap = {
    "Temporary Dexterity": "临时敏捷",
    "Temporary Strength": "临时力量",
    "Temporary Strength Down": "临时失去力量",
    "Temporary Focus": "临时集中",
}

hardcoded_powers = [
    {
        "id": "scrutiny", "name": "细看",
        "description": "在你的回合内你无法额外抽牌。",
        "type": "Buff", "stack_type": "Single", "allow_negative": False,
        "image": "scrutiny_power.png", "page": "细看",
    },
    {
        "id": "grasp", "name": "紧握",
        "description": "每当你打出一张牌，就失去[[File:colorless_energy_icon.png|16px|link=能量]]。",
        "type": "Buff", "stack_type": "Single", "allow_negative": False,
        "image": "grasp_power.png", "page": "紧握",
    },
    {
        "id": "hunger", "name": "饥饿",
        "description": "当你打出攻击牌或技能牌时，将其{{颜色|gold|消耗}}。",
        "type": "Buff", "stack_type": "Single", "allow_negative": False,
        "image": "hunger_power.png", "page": "饥饿",
    },
]
data += hardcoded_powers

for power in data:
    if power.get("description"):
        power["description"] = clean_text(power["description"], power.get("color")).replace("this creature", "该怪物").replace("[Amount]", "X")
    power["category"] = "power"
    power["id"] = power["id"].lower()
    power["image"] = f'{power["id"]}_power.png'
    if power["name"] in name_remap:
        power["name"] = name_remap[power["name"]]
    power["page"] = power["name"]
    power["allow_negative"] = str(power["allow_negative"])

field_order = [
    "category",
    "id",
    "name",
    "description",
    "type",
    "stack_type",
    "allow_negative",
    "image",
    "page",
]
result = [[power.get(field) for field in field_order] for power in data]

pagedata = json.dumps({
    "sources": f"导出数据自版本 {ver}",
    "schema": {
        "fields": process_fields(field_order),
    },
    "data": result,
}, ensure_ascii=False, indent=2)

with open(f"powers_{ver}.json", "w", encoding="utf-8") as f:
    f.write(pagedata)
# site.pages["Data:Power.tabx"].save(pagedata, summary=f"导出数据自版本 {ver}")