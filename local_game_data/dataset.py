from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .csharp import Evaluator, ExtractionError, SourceIndex, Symbol, Var, ForeignModel, LocValue, children, slug, text
from .formatting import number, render


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    if isinstance(value, Symbol):
        return value.name.split(".")[-1]
    if isinstance(value, Var):
        return json_default(value.value) if isinstance(value.value, (Decimal, Symbol, Var)) else value.value
    raise TypeError(type(value).__name__)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")


class Dataset:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"本地数据尚未生成：{self.path}。请先运行 python -m local_game_data export --source ../sts2_source_code --version <版本号>")
        self.document = json.loads(self.path.read_text(encoding="utf-8"))
        if self.document.get("schema_version") != 1:
            raise ValueError("Unsupported local dataset schema")
        self.version = self.document["version"]

    def get(self, route):
        route = route.strip("/")
        if route not in self.document["routes"]:
            raise ValueError(f"Unknown local game data route: {route}")
        return deepcopy(self.document["routes"][route])

    @property
    def items(self):
        return deepcopy(self.document["items"])


class Extractor:
    def __init__(self, source, version, lang):
        self.source = Path(source).resolve()
        self.version, self.lang = version.removeprefix("v"), lang
        self.index = SourceIndex(self.source)
        loc = self.source / "localization" / lang
        if not loc.is_dir():
            raise ExtractionError(f"Missing localization directory {loc}")
        self.tables = {p.stem: json.loads(p.read_text(encoding="utf-8-sig")) for p in sorted(loc.glob("*.json"))}
        self.index.tables = self.tables
        self.issues, self.counts = [], {}
        self.routes = {k: [] for k in ("cards", "relics", "potions", "powers", "monsters", "events", "ancients", "keywords", "enchantments", "afflictions")}
        self.card_variants = []
        self.pools = self.get_pools()

    def issue(self, record, field, error, severity="error"):
        entry = {"category": record.get("_category"), "id": record["id"], "field": field,
                 "message": str(error), "source": record.get("_source"), "severity": severity}
        if entry not in self.issues:
            self.issues.append(entry)
        if severity == "error":
            record.setdefault("_errors", {})[field] = str(error)

    def attempt(self, record, field, fn, severity="error"):
        try:
            value = fn()
            if isinstance(value, Symbol): value = value.name.split(".")[-1]
            record[field] = value
            return value
        except (ExtractionError, KeyError, IndexError, TypeError, ValueError, ArithmeticError) as exc:
            self.issue(record, field, exc, severity)
            return None

    def get_pools(self):
        result = {}
        for category, pool_base, method in (("cards", "CardPoolModel", "GenerateAllCards"), ("relics", "RelicPoolModel", "GenerateAllRelics"), ("potions", "PotionPoolModel", "GenerateAllPotions")):
            mapping = {}
            for pool in self.index.models(pool_base):
                if pool.name.startswith(("Mock", "Test")): continue
                ev = Evaluator(self.index, pool)
                try:
                    title = ev.member("Title") if category == "cards" else ev.member("EnergyColorName")
                except ExtractionError:
                    continue
                try:
                    _, nodes = self.index.find(pool.key, method)
                except ExtractionError:
                    continue
                pool_source = text(nodes[0])
                for typ, member in re.findall(r"(\w+Epoch)\.(Cards|Potions|Relics)", pool_source):
                    try:
                        _, definitions = self.index.find(typ, member)
                        pool_source += "\n" + text(definitions[0])
                    except ExtractionError:
                        continue
                for name in re.findall(r"ModelDb\.(?:Card|Relic|Potion)<(\w+)>", pool_source):
                    mapping.setdefault(name, []).append({"pool": title, "pool_class": pool.name})
            result[category] = mapping
        return result

    def record(self, model, category):
        entry = slug(model.name)
        public_id = entry.removesuffix("_POWER") if category == "powers" else entry
        return {"id": public_id, "_model_id": entry, "_category": category,
                "_class": model.name, "_source": str(model.path.relative_to(self.source)),
                "_deprecated": model.name.startswith("Deprecated")}

    def loc(self, table, key):
        if key not in self.tables.get(table, {}):
            raise ExtractionError(f"Missing localization key {table}.{key}")
        return self.tables[table][key]

    def localized_property(self, ev, prop, record, extra=None):
        value = ev.member(prop)
        if isinstance(value, LocValue):
            context = self.context(ev, record)
            context.update(extra or {})
            context.update(value.variables)
            return render(self.loc(value.table, value.key), context)
        if isinstance(value, str):
            return value
        raise ExtractionError(f"Unexpected localization value for {prop}: {value}")

    def context(self, ev, record):
        return {**ev.values["DynamicVars"], "IfUpgraded": bool(ev.upgraded), "InCombat": False,
                "OnTable": False, "IsTargeting": False, "IsOstyAlive": False,
                "GainsBlock": lambda: ev.optional("GainsBlock", False),
                "TargetType": number(ev.values.get("TargetType", "None")),
                "energyPrefix": record.get("color", record.get("pool", "colorless")) or "colorless",
                "singleStarIcon": "[STAR]", "Amount": 1}

    def variables(self, ev, record):
        try:
            ev.initialize_vars()
        except (ExtractionError, KeyError, IndexError, TypeError, ValueError) as exc:
            self.issue(record, "dynamic_vars", exc)

    def pool(self, model, record, field):
        pools = self.pools[record["_category"]].get(model.name, [])
        record["_pools"] = pools
        if not pools:
            self.issue(record, field, f"{model.name} is not present in any content pool")
        else:
            record[field] = pools[0]["pool"]

    def cards(self):
        models = list(self.index.models("CardModel"))
        self.counts["cards"] = len(models)
        for model in models:
            ev = Evaluator(self.index, model)
            base = self.record(model, "cards")
            self.pool(model, base, "color")
            try:
                ev.card_constructor()
            except (ExtractionError, KeyError, TypeError, ValueError) as exc:
                self.issue(base, "constructor", exc)
            self.variables(ev, base)
            try:
                ev.values["Keywords"] = ev.member("CanonicalKeywords")
            except ExtractionError as exc:
                self.issue(base, "keywords", exc)
            self.attempt(base, "name", lambda: self.localized_property(ev, "Title", base))
            for key, prop in (("type", "Type"), ("rarity", "Rarity")):
                self.attempt(base, key, lambda prop=prop: ev.member(prop, model.key) if prop in model.members else ev.member(prop))
            constraint = self.attempt(base, "multiplayer_constraint", lambda: ev.member("MultiplayerConstraint"))
            base["multiplayer_only"] = constraint == "MultiplayerOnly" if constraint else None
            max_upgrade = self.attempt(base, "max_upgrade_level", lambda: ev.member("MaxUpgradeLevel"))
            try:
                star_cost = ev.member("CanonicalStarCost", model.key)
                ev.values["BaseStarCost"] = star_cost
            except ExtractionError as exc:
                self.issue(base, "star_cost", exc)
            for level in (0, 1) if max_upgrade is not None and max_upgrade > 0 else (0,):
                record = deepcopy(base)
                if level:
                    ev.upgraded = 1
                    ev.values.update(CurrentUpgradeLevel=1, IsUpgraded=True)
                    try:
                        ev.upgrade()
                    except (ExtractionError, KeyError, TypeError, ValueError) as exc:
                        self.issue(record, "upgrade", exc)
                    self.attempt(record, "name", lambda: self.localized_property(ev, "Title", record))
                record["upgrades"] = level
                record["star_cost"] = ev.values["BaseStarCost"]
                self.attempt(record, "cost", lambda: "X" if ev.member("HasEnergyCostX") else str(ev.values["EnergyCost"]["cost"]) if ev.values["EnergyCost"]["cost"] >= 0 else "")
                self.attempt(record, "description", lambda: self.card_description(ev, record))
                record["_variables"] = deepcopy(ev.values["DynamicVars"])
                self.card_variants.append(record)
                if not level:
                    self.routes["cards"].append(deepcopy(record))

    def card_description(self, ev, record):
        context = self.context(ev, record)
        # Extra description arguments are normally absent. Read the named
        # assignments from overrides; unbound loop variables stay unresolved.
        try:
            _, methods = self.index.find(ev.model.key, "AddExtraArgsToDescription")
            from .csharp import walk
            for node in walk(methods[0]):
                if node.type == "invocation_expression" and text(node.child_by_field_name("function")) == "description.Add":
                    try:
                        args = ev.args(node, {})
                        if len(args) == 2: context[args[0]] = args[1]
                    except ExtractionError:
                        continue
        except ExtractionError:
            pass
        template = self.tables["cards"].get(record["_model_id"] + ".description")
        if template is None:
            if not ev.values.get("Keywords"):
                raise ExtractionError(f"Missing card description and keywords: {record['_model_id']}")
            template = ""
        body = render(template, context)
        keywords = ev.values.get("Keywords")
        if keywords is None:
            keywords = ev.member("CanonicalKeywords")
            ev.values["Keywords"] = list(keywords)
        names = [number(k) for k in keywords]
        # These orders are read from the recovered game, not alphabetically sorted.
        order_path = self.source / "src/Core/Entities/Cards/CardKeywordOrder.cs"
        order = order_path.read_text(encoding="utf-8")
        before = order.split("beforeDescription", 1)[1].split("afterDescription", 1)[0]
        after = order.split("afterDescription", 1)[1]
        def keyword_lines(section):
            for key in re.findall(r"CardKeyword\.(\w+)", section):
                if key in names:
                    yield "[gold]" + self.loc("card_keywords", slug(key) + ".title") + "[/gold]" + self.loc("card_keywords", "PERIOD")
        lines = [*keyword_lines(before), body]
        replay = ev.optional("BaseReplayCount", 0)
        if replay:
            lines.append(render(self.loc("static_hover_tips", "REPLAY.extraText"), {"Times": replay}))
        lines.extend(keyword_lines(after))
        return "\n".join(line for line in lines if line)

    def simple_models(self, category, base_type):
        models = list(self.index.models(base_type))
        self.counts[category] = len(models)
        for model in models:
            ev = Evaluator(self.index, model)
            record = self.record(model, category)
            entry = record["_model_id"]
            self.variables(ev, record)
            self.attempt(record, "name", lambda: self.localized_property(ev, "Title", record))
            if category in ("relics", "potions"):
                self.pool(model, record, "pool")
                record["color"] = record.get("pool")
                self.attempt(record, "rarity", lambda: ev.member("Rarity"))
            if category == "relics":
                self.attempt(record, "flavor", lambda: self.loc(category, entry + ".flavor"))
                record["flavorText"] = record.get("flavor")
                record["tier"] = record.get("rarity")
            if category == "powers":
                for field, prop in (("type", "Type"), ("stack_type", "StackType"), ("allow_negative", "AllowNegative")):
                    self.attempt(record, field, lambda prop=prop: ev.member(prop))
            self.attempt(record, "description", lambda: self.localized_property(ev, "Description", record))
            if category == "powers":
                record["smart_description_template"] = self.tables[category].get(entry + ".smartDescription")
            self.routes[category].append(record)

    def monsters(self):
        models = list(self.index.models("MonsterModel"))
        self.counts["monsters"] = len(models)
        for model in models:
            ev = Evaluator(self.index, model)
            record = self.record(model, "monsters")
            self.attempt(record, "name", lambda: self.localized_property(ev, "Title", record, {"Count": ev.optional("_baseTestSubjectNum", 8)}))
            forms = [key for key in ("FirstFormHp", "SecondFormHp", "ThirdFormHp") if key in model.members]
            for ascension, suffix in ((False, ""), (True, "_ascension")):
                ev.ascension = ascension
                for field, prop in (("min_hp", "MinInitialHp"), ("max_hp", "MaxInitialHp")):
                    self.attempt(record, field + suffix, lambda prop=prop: " / ".join(number(ev.member(k)) for k in forms) if len(forms) > 1 else ev.member(prop))
            self.routes["monsters"].append(record)

    def events(self):
        models = list(self.index.models("EventModel"))
        self.counts["events"] = len(models)
        for model in models:
            ev = Evaluator(self.index, model)
            record = self.record(model, "events")
            ancient = self.index.derives(model.key, "AncientEventModel")
            table = ev.member("LocTable")
            entry = record["_model_id"]
            self.attempt(record, "name", lambda: self.loc(table, entry + ".title"))
            record["_ancient"] = ancient
            record["pages"] = {k: v for k, v in self.tables[table].items() if k.startswith(entry + ".")}
            record["description"] = self.tables[table].get(entry + ".pages.INITIAL.description", "")
            record["options"] = []
            record["relics"] = sorted({slug(x) for x in re.findall(r"RelicOption<(\w+)>", text(model.node))})
            for key, value in record["pages"].items():
                if ".pages.INITIAL.options." in key and key.endswith(".title"):
                    record["options"].extend((value, record["pages"].get(key[:-6] + ".description", "")))
            self.routes["events"].append(record)
            if ancient: self.routes["ancients"].append(deepcopy(record))

    def keywords(self):
        for table in ("card_keywords", "static_hover_tips"):
            for key, title in self.tables[table].items():
                if not key.endswith(".title") or key.startswith("NONE."): continue
                entry = key[:-6]
                desc_key = entry + ".description"
                if desc_key not in self.tables[table]: continue
                record = {"id": entry, "name": title, "_category": "keywords", "_source": f"localization/{self.lang}/{table}.json"}
                context = {"Amount": "X", "Times": "X", "Cards": "X", "Damage": "X", "Block": "X", "Energy": "X", "energyPrefix": "colorless", "singleStarIcon": "[STAR]"}
                if entry == "ARROW":
                    context["ShowArrowCount"] = False  # HoverTipFactory.ForArrow outside combat.
                if entry == "SUMMON_DYNAMIC":
                    context["Summon"] = "X"  # Generic keyword, not a concrete card amount.
                self.attempt(record, "description", lambda: render(self.tables[table][desc_key], context))
                self.routes["keywords"].append(record)

    def build(self):
        self.cards()
        for category, base in (("relics", "RelicModel"), ("potions", "PotionModel"), ("powers", "PowerModel"), ("enchantments", "EnchantmentModel"), ("afflictions", "AfflictionModel")):
            self.simple_models(category, base)
        self.monsters()
        self.events()
        self.keywords()
        for route in self.routes.values():
            route.sort(key=lambda r: r["id"])
            ids = [r["id"] for r in route]
            if len(ids) != len(set(ids)):
                raise ExtractionError("Duplicate record IDs in extracted dataset")
        items = {key: deepcopy(self.routes[key]) for key in ("relics", "potions", "enchantments", "afflictions", "keywords")}
        items["cards"] = self.card_variants
        items["events"] = [deepcopy(e) for e in self.routes["events"] if not e["_ancient"]]
        items["creatures"] = deepcopy(self.routes["monsters"])
        for creature in items["creatures"]:
            creature.update(type="Monster", minHP=creature.get("min_hp"), maxHP=creature.get("max_hp"), minHPA=creature.get("min_hp_ascension"), maxHPA=creature.get("max_hp_ascension"))
        hashes = {}
        for file in sorted((self.source / "src/Core").rglob("*.cs")):
            hashes[str(file.relative_to(self.source))] = hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted((self.source / "localization" / self.lang).glob("*.json")):
            hashes[str(file.relative_to(self.source))] = hashlib.sha256(file.read_bytes()).hexdigest()
        return {"schema_version": 1, "version": self.version, "lang": self.lang, "source": str(self.source),
                "input_hash": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
                "model_counts": self.counts, "placeholders": self.index.placeholders,
                "routes": self.routes, "items": items, "issues": self.issues}


def extract(source, version, output, lang="zhs", dataset_file=None):
    document = Extractor(source, version, lang).build()
    write_json(Path(dataset_file) if dataset_file is not None else Path(output) / "dataset.json", document)
    write_json(Path(output) / "report.json", {k: document[k] for k in ("version", "source", "input_hash", "model_counts", "placeholders", "issues")})
    return document
