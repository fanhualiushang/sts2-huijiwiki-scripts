"""Conservative static extraction of act placement and self-applied spawn buffs.

An unresolved branch/call is a metadata gap. It is never treated as no buff;
the Wiki merger can then retain the formal table's existing information.
"""
import re

from .csharp import Evaluator, ExtractionError, children, slug, text, walk
from .formatting import number


def encounter_metadata(extractor):
    index = extractor.index
    result = {}
    for act in index.models("ActModel"):
        ev = Evaluator(index, act)
        try:
            stage = extractor.localized_property(ev, "Title", {"id": slug(act.name)})
            _, definitions = index.find(act.key, "GenerateAllEncounters")
        except ExtractionError:
            continue
        for name in set(re.findall(r"ModelDb\.Encounter<(\w+)>", text(definitions[0]))):
            encounter = index.types.get(name)
            if encounter is None: continue
            ee = Evaluator(index, encounter)
            try:
                room = number(ee.member("RoomType"))
                _, possible = index.find(encounter.key, "AllPossibleMonsters")
            except ExtractionError:
                continue
            names = set(re.findall(r"ModelDb\.Monster<(\w+)>", text(possible[0])))
            # Follow a static dictionary/property such as _raiderValidCounts.Keys.
            for identifier in re.findall(r"\b[A-Za-z_]\w*\b", text(possible[0])):
                if identifier in encounter.members:
                    nodes = encounter.members[identifier]
                    if isinstance(nodes, list):
                        names.update(re.findall(r"ModelDb\.Monster<(\w+)>", text(nodes[0])))
            for monster in names:
                entry = result.setdefault(monster, {"stages": set(), "tiers": set(), "sources": []})
                entry["stages"].add(stage)
                # A multi-monster boss/elite encounter can contain ordinary
                # minions. Do not label all of those monsters as bosses/elites.
                if room == "Monster": entry["tiers"].add("普通")
                elif len(names) == 1 and room in ("Boss", "Elite"):
                    entry["tiers"].add("Boss" if room == "Boss" else "精英")
                entry["sources"].append(str(encounter.path.relative_to(extractor.source)))
    return result


def _active_condition(ev, call, body):
    parent = call.parent
    while parent is not None and parent != body:
        if parent.type in ("for_statement", "foreach_statement", "while_statement", "switch_section"):
            raise ExtractionError("Spawn power is inside a runtime loop/switch")
        if parent.type == "if_statement":
            active = bool(ev.eval(parent.child_by_field_name("condition")))
            branch = parent.child_by_field_name("consequence")
            in_true_branch = branch is not None and branch.start_byte <= call.start_byte < branch.end_byte
            if active != in_true_branch: return False
        parent = parent.parent
    return True


def _spawn_powers(extractor, model, ascension):
    index = extractor.index
    ev = Evaluator(index, model, ascension=ascension)
    ev.values.update(IsMutable=True, IsCanonical=False)
    _, methods = index.find(model.key, "AfterAddedToRoom")
    method = methods[0]
    powers = []
    for call in walk(method):
        if call.type != "invocation_expression": continue
        function = text(call.child_by_field_name("function"))
        if function.startswith("base.AfterAddedToRoom"):
            # A nontrivial inherited hook needs its own analysis, not omission.
            owner, inherited = index.find(model.base, "AfterAddedToRoom")
            if owner.name != "MonsterModel" and "PowerCmd.Apply" in text(inherited[0]):
                raise ExtractionError("Spawn powers include a nontrivial inherited hook")
        if not function.startswith("PowerCmd.Apply"): continue
        args = children(call.child_by_field_name("arguments"), "argument")
        if not args:
            args = children(next(iter(children(call, "argument_list")), None), "argument")
        generic = re.fullmatch(r"PowerCmd\.Apply<(\w+)>", function)
        if not generic:
            raise ExtractionError("Spawn power uses an instance rather than a generic power type")
        if len(args) < 3:
            raise ExtractionError("Incomplete spawn power application")
        target = text(args[1]).strip()
        if target not in ("base.Creature", "Creature"):
            # Only the monster's own starting buffs belong in this column.
            if any(s in target for s in ("Opponents", "Player", "targets")): continue
            raise ExtractionError("Spawn power target cannot be proven to be this monster")
        if not _active_condition(ev, call, method): continue
        amount = ev.eval(args[2].named_children[-1])
        if not isinstance(amount, (int, float)):
            from decimal import Decimal
            if not isinstance(amount, Decimal): raise ExtractionError("Non-numeric spawn power amount")
        if amount <= 0: continue
        powers.append((generic[1], amount))
    return powers


def starting_power_text(extractor, model, power_lookup):
    normal = _spawn_powers(extractor, model, False)
    ascended = _spawn_powers(extractor, model, True)
    if not normal:
        return None  # Encounter/phase setup may still supply starting powers.
    if [x[0] for x in normal] != [x[0] for x in ascended]:
        raise ExtractionError("Starting power presence varies with ascension")
    parts = []
    for (name, amount), (_, high) in zip(normal, ascended):
        power = power_lookup.get(name)
        if power is None or not power.get("name"):
            raise ExtractionError(f"Missing power name for starting buff {name}")
        # "饥饿" also names a different historical power on this Wiki.
        # Use its stable ID even when there is no existing monster reference.
        title = "ravenous" if power["id"] == "RAVENOUS" else power.get("_wiki_page", power["name"])
        part = "{{状态|" + title + "}}"
        if power.get("stack_type") != "Single":
            part += number(amount)
        if high != amount:
            part += " （高进阶" + number(high) + "）"
        parts.append(part)
    return "".join(parts)
