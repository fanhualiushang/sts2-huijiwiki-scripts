import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from utils import *

monster_data = load_wiki_records(wiki_page("monsters"))

data = get_data_by_api("monsters")

for monster in data:
    monster["category"] = "monster_beta"
    monster["icon"] = monster_data.get(monster["id"].lower(), {}).get("icon")
    monster["id"] = monster["id"].lower()
    monster["image"] = f'{monster["id"]}.png'
    monster["minHP"] = str(monster.get("min_hp", ""))
    if monster.get('max_hp'):
        monster["maxHP"] = str(monster["max_hp"])
    else:
        monster["maxHP"] = monster["minHP"]
    if monster["min_hp_ascension"]:
        monster["ascenderminHP"] = str(monster["min_hp_ascension"])
    else:
        monster["ascenderminHP"] = monster["minHP"]
    if monster["max_hp_ascension"]:
        monster["ascendermaxHP"] = str(monster["max_hp_ascension"])
    else:
        monster["ascendermaxHP"] = monster["ascenderminHP"]
    monster['tier'] = monster_data.get(monster['id'], {}).get('tier', '')
    if not monster['tier']:
        monster['tier'] = ''
    monster['power'] = monster_data.get(monster['id'], {}).get('power', '')
    if not monster['power']:
        monster['power'] = ''
    monster['stage'] = monster_data.get(monster['id'], {}).get('stage', '')
    if not monster['stage']:
        monster['stage'] = ''
    monster['note'] = monster_data.get(monster['id'], {}).get('note', '')
    if monster['note'] == '' or not monster['note']:
        monster['note'] = '无'
    monster['page'] = monster['name']
    if monster['id'] == 'test_subject':
        monster['page'] = '实验体'

field_order = [
    "category",
    "id",
    "name",
    "image",
    "minHP",
    "maxHP",
    "ascenderminHP",
    "ascendermaxHP",
    "tier",
    "power",
    "stage",
    "note",
    "page",
    "icon",
]
result = [[monster.get(field) for field in field_order] for monster in data]
pagedata = json.dumps({
    "sources": f"导出数据自版本 {ver}",
    "schema": { "fields": process_fields(field_order) },
    "data": result,
}, ensure_ascii=False)

pagedata = write_tabx(f"monsters_{ver}.json", pagedata)
# site.pages[wiki_page("monsters")].save(pagedata, summary=f"导出数据自版本 {ver}")
