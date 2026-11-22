"""Resolve event option relics through local collection/helper references."""
import re

from .csharp import ExtractionError, text, walk


FRAMEWORK_TYPES = {"EventModel", "AncientEventModel", "AbstractModel"}


def option_relic_classes(index, model):
    # Keep support for direct generic options anywhere in the event declaration.
    relics = set(re.findall(r"RelicOption<(\w+)>", text(model.node)))
    roots = ["AllPossibleOptions", "GenerateInitialOptions"]
    roots.extend(name for name in model.members if name.endswith("Options"))
    pending, visited = list(roots), set()
    sources = []
    while pending:
        name = pending.pop()
        if name in visited: continue
        visited.add(name)
        try:
            owner, definitions = index.find(model.key, name)
        except ExtractionError:
            continue
        if owner.name in FRAMEWORK_TYPES or not isinstance(definitions, list):
            continue
        for definition in definitions:
            found = set(re.findall(r"ModelDb\.Relic<(\w+)>\(\)", text(definition)))
            relics.update(found)
            if found:
                sources.append({"member": owner.name + "." + name,
                                "line": definition.start_point.row + 1,
                                "relic_classes": sorted(found)})
            for node in walk(definition):
                if node.type == "identifier" and text(node) not in visited:
                    # Only event-owned members are followed. Ordinary local
                    # variables and framework APIs cannot expand into all relics.
                    try:
                        dependency_owner, _ = index.find(model.key, text(node))
                    except ExtractionError:
                        continue
                    if dependency_owner.name not in FRAMEWORK_TYPES:
                        pending.append(text(node))
    return relics, sources
