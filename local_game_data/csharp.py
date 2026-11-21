"""Read a decompiled C# project without loading or executing game assemblies.

The evaluator deliberately implements only the data-producing part of C# used by
the models. Unsupported expressions raise ExtractionError rather than becoming 0.
"""
from __future__ import annotations

import json
import operator
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from tree_sitter import Language, Parser
import tree_sitter_c_sharp


class ExtractionError(ValueError):
    pass


def text(node):
    return node.text.decode("utf-8") if node is not None else ""


def children(node, kind):
    return [n for n in node.named_children if n.type == kind] if node else []


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def slug(name):
    return re.sub(r"[^A-Z0-9_]", "", re.sub(r"(?<=[A-Za-z0-9])(?=[A-Z])", "_", name.strip()).upper())


@dataclass
class TypeDef:
    name: str
    namespace: str
    base: str
    node: object
    path: Path
    members: dict = field(default_factory=dict)
    constructors: list = field(default_factory=list)

    @property
    def key(self):
        return self.namespace + "." + self.name


class SourceIndex:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.types = {}
        self.placeholders = []
        parser = Parser(Language(tree_sitter_c_sharp.language()))
        paths = sorted((self.root / "src/Core/Models").rglob("*.cs"))
        paths += sorted((self.root / "src/Core/Localization/DynamicVars").glob("*.cs"))
        paths += sorted((self.root / "src/Core/Entities").rglob("*.cs"))
        paths += sorted((self.root / "src/Core/ValueProps").glob("*.cs"))
        paths += sorted((self.root / "src/Core/Timeline/Epochs").glob("*.cs"))
        room_type = self.root / "src/Core/Rooms/RoomType.cs"
        if room_type.is_file(): paths.append(room_type)
        for path in paths:
            source = path.read_bytes()
            if not source.strip():
                self.placeholders.append(str(path.relative_to(self.root)))
                continue
            tree = parser.parse(source)
            ns = next((text(n.child_by_field_name("name")) for n in tree.root_node.named_children
                       if n.type in ("file_scoped_namespace_declaration", "namespace_declaration")), "")
            for node in tree.root_node.named_children:
                if node.type not in ("class_declaration", "enum_declaration"):
                    continue
                name = text(node.child_by_field_name("name"))
                bases = children(node, "base_list")
                base = text(bases[0].named_children[0]).split("<")[0] if bases else ""
                typedef = TypeDef(name, ns, base, node, path)
                body = node.child_by_field_name("body")
                if body is None:
                    continue
                enum_value = -1
                for member in body.named_children:
                    kind = member.type
                    if kind == "constructor_declaration":
                        typedef.constructors.append(member)
                    elif kind in ("property_declaration", "method_declaration", "enum_member_declaration"):
                        key = text(member.child_by_field_name("name"))
                        typedef.members.setdefault(key, []).append(member)
                        if kind == "enum_member_declaration":
                            value = member.child_by_field_name("value")
                            try:
                                enum_value = int(text(value), 0) if value else enum_value + 1
                            except ValueError:
                                # Flag expressions are retained symbolically by member access.
                                enum_value += 1
                            typedef.members[key] = enum_value
                    elif kind == "field_declaration":
                        for declaration in children(member, "variable_declaration"):
                            for var in children(declaration, "variable_declarator"):
                                typedef.members.setdefault(text(var.child_by_field_name("name")), []).append(var)
                self.types[typedef.key] = typedef
                self.types.setdefault(name, typedef)
        for required in ("CardModel", "PowerModel", "RelicModel", "PotionModel", "MonsterModel"):
            if required not in self.types:
                raise ExtractionError(f"Missing recovered C# type: {required}")
        for parent in list(self.types.values()):
            if parent.node.type != "class_declaration": continue
            for enum in children(parent.node.child_by_field_name("body"), "enum_declaration"):
                short = parent.name + "." + text(enum.child_by_field_name("name"))
                if short in self.types: continue
                typ = TypeDef(short, parent.namespace, "", enum, parent.path)
                value = -1
                for item in children(enum.child_by_field_name("body"), "enum_member_declaration"):
                    explicit = item.child_by_field_name("value")
                    value = int(text(explicit), 0) if explicit else value + 1
                    typ.members[text(item.child_by_field_name("name"))] = value
                self.types[short] = self.types[typ.key] = typ

    def derives(self, name, base):
        seen = set()
        while name in self.types and name not in seen:
            if name == base or self.types[name].name == base:
                return True
            seen.add(name)
            name = self.types[name].base
        return False

    def find(self, name, member):
        seen = set()
        while name in self.types and name not in seen:
            seen.add(name)
            typedef = self.types[name]
            if member in typedef.members:
                return typedef, typedef.members[member]
            name = typedef.base
        raise ExtractionError(f"Unknown C# member {name}.{member}")

    def models(self, base):
        seen = set()
        for item in self.types.values():
            if item.key in seen:
                continue
            seen.add(item.key)
            modifiers = [text(n) for n in children(item.node, "modifier")]
            if item.name != base and "abstract" not in modifiers and self.derives(item.key, base):
                if re.search(r"\.(Mocks?|TestSupport)(\.|$)", item.namespace):
                    continue
                mock = item.members.get("IsMock")
                if mock and "=> true" in text(mock[0]):
                    continue
                yield item


@dataclass
class Symbol:
    name: str


@dataclass
class Var:
    name: str
    value: object
    calculated: bool = False


@dataclass
class ModelRef:
    name: str


@dataclass
class ForeignModel:
    name: str


@dataclass
class LocValue:
    table: str
    key: str
    variables: dict = field(default_factory=dict)


class Returned(Exception):
    def __init__(self, value):
        self.value = value


ALIASES = {x: x + "Power" for x in ("Dexterity", "Doom", "Poison", "Strength", "Vulnerable", "Weak")}


class Evaluator:
    def __init__(self, index: SourceIndex, model: TypeDef, upgraded=0, ascension=False):
        self.index, self.model = index, model
        self.upgraded, self.ascension = upgraded, ascension
        self.values = {
            "CurrentUpgradeLevel": upgraded, "IsUpgraded": bool(upgraded), "DynamicVars": {},
            "EnergyCost": {"cost": 0}, "CanonicalStarCost": 0, "BaseStarCost": 0,
            "Amount": 1, "IsMutable": False, "IsCanonical": True,
        }
        self.active = set()
        self.current_type = model.key

    def fail(self, node, message="Unsupported C# expression"):
        raise ExtractionError(f"{message}: {text(node)[:220]}")

    def member(self, name, owner=None):
        if owner is None and name in self.values:
            return self.values[name]
        owner = owner or self.model.key
        key = (owner, name)
        if key in self.active:
            raise ExtractionError(f"Circular C# property: {owner}.{name}")
        typedef, nodes = self.index.find(owner, name)
        if isinstance(nodes, int):
            return nodes
        node = nodes[0]
        previous = self.current_type
        self.current_type = typedef.key
        self.active.add(key)
        try:
            if node.type == "variable_declarator":
                value = next((n for n in node.named_children if n != node.child_by_field_name("name")), None)
                if value is None:
                    # These are C#'s specified field defaults, not guesses for
                    # expressions that failed to parse.
                    typ = text(node.parent.child_by_field_name("type"))
                    if typ in ("int", "long", "short", "decimal", "float", "double", "byte"):
                        return 0
                    if typ == "bool":
                        return False
                    if typ in self.index.types and self.index.types[typ].node.type == "enum_declaration":
                        enum = self.index.types[typ]
                        enum_key = next((k for k, v in enum.members.items() if v == 0), None)
                        if enum_key:
                            return Symbol(typ + "." + enum_key)
                    if typ.endswith("?") or typ in ("string", "object"):
                        return None
                    raise ExtractionError(f"Unknown field default {typedef.name}.{name}: {typ}")
                return self.eval(value)
            arrow = next(iter(children(node, "arrow_expression_clause")), None)
            if arrow:
                return self.eval(arrow.named_children[0])
            accessor_list = next(iter(children(node, "accessor_list")), None)
            if accessor_list:
                accessor = next((n for n in accessor_list.named_children if text(n).lstrip().startswith("get")), None)
                if accessor:
                    return self.run_member(accessor)
            if node.type == "method_declaration":
                return self.run_member(node)
            self.fail(node, f"Property has no evaluable getter {typedef.name}.{name}")
        finally:
            self.active.remove(key)
            self.current_type = previous

    def optional(self, name, default):
        try:
            self.index.find(self.model.key, name)
        except ExtractionError:
            return default
        return self.member(name)

    def args(self, node, env):
        arguments = node.child_by_field_name("arguments")
        if arguments is None:
            arguments = next(iter(children(node, "argument_list") + children(node, "bracketed_argument_list")), None)
        result = []
        for arg in children(arguments, "argument"):
            if not arg.named_children and text(arg) in ("this", "base"):
                result.append(ModelRef(self.model.key))
            elif arg.named_children:
                result.append(self.eval(arg.named_children[-1], env))
            else:
                self.fail(arg, "Unsupported C# argument")
        return result

    def eval(self, node, env=None):
        if node is None:
            raise ExtractionError("Missing C# expression")
        env = {} if env is None else env
        kind, raw = node.type, text(node)
        named = node.named_children
        if raw == "SaveManager.Instance.Progress.TestSubjectKills":
            # Wiki preview uses the initial displayed specimen number.
            return 0
        if kind == "argument":
            return self.eval(named[-1], env)
        if kind in ("integer_literal", "real_literal"):
            value = re.sub(r"[mMdDfFuUlL]+$", "", raw).replace("_", "")
            return Decimal(value) if any(c in raw for c in ".mMdDfF") else int(value, 0) if value.startswith("0x") else int(value)
        if kind in ("string_literal", "character_literal"):
            try:
                return json.loads(raw) if raw.startswith('"') else raw[1:-1]
            except ValueError:
                self.fail(node, "Invalid C# string")
        if kind == "verbatim_string_literal":
            return raw[2:-1].replace('""', '"')
        if kind == "boolean_literal":
            return raw == "true"
        if kind == "null_literal":
            return None
        if kind in ("this_expression", "base_expression") or raw in ("base", "this"):
            owner = self.index.types[self.current_type].base if raw == "base" else self.model.key
            return ModelRef(owner)
        if kind == "identifier":
            if raw in env:
                return env[raw]
            try:
                return self.member(raw)
            except ExtractionError:
                if raw in self.index.types or raw in ("Math", "MathF", "Mathf", "Array", "AscensionHelper", "Convert"):
                    return Symbol(raw)
                raise
        if kind in ("parenthesized_expression", "checked_expression", "arrow_expression_clause", "ref_expression"):
            return self.eval(named[-1], env)
        if kind == "cast_expression":
            value = self.eval(node.child_by_field_name("value") or named[-1], env)
            typ = text(node.child_by_field_name("type"))
            if typ in ("int", "long", "short"):
                return int(value)
            return value
        if kind == "member_access_expression":
            expression = node.child_by_field_name("expression")
            name = text(node.child_by_field_name("name"))
            if text(expression) in self.index.types:
                typ = self.index.types[text(expression)]
                if typ.node.type == "enum_declaration" and name in typ.members:
                    return Symbol(typ.name + "." + name)
            obj = self.eval(expression, env)
            if isinstance(obj, ModelRef):
                if name == "Id":
                    return {"Entry": slug(self.model.name)}
                return self.member(name, None if name in self.values else obj.name)
            if isinstance(obj, ForeignModel):
                ev = Evaluator(self.index, self.index.types[obj.name])
                return ev.member(name)
            if isinstance(obj, Symbol):
                if obj.name + "." + name in self.index.types:
                    return Symbol(obj.name + "." + name)
                if obj.name in self.index.types:
                    if self.index.types[obj.name].node.type == "enum_declaration":
                        return Symbol(obj.name + "." + name)
                    try:
                        return self.member(name, obj.name)
                    except ExtractionError:
                        if self.index.types[obj.name].node.type != "enum_declaration":
                            raise
                return Symbol(obj.name + "." + name)
            if isinstance(obj, Var):
                if name in ("BaseValue", "PreviewValue", "EnchantedValue", "IntValue", "StringValue", "BoolVal"):
                    return int(obj.value) if name == "IntValue" else obj.value
                if name == "Name":
                    return obj.name
            if isinstance(obj, dict):
                key = ALIASES.get(name, name)
                if key in obj:
                    return obj[key]
                raise ExtractionError(f"Unknown dynamic variable {key}")
            if name in ("Length", "Count") and isinstance(obj, (list, str)):
                return len(obj)
            self.fail(node)
        if kind == "element_access_expression":
            obj = self.eval(node.child_by_field_name("expression"), env)
            return obj[self.args(node, env)[0]]
        if kind in ("array_creation_expression", "implicit_array_creation_expression", "collection_expression", "initializer_expression"):
            init = next(iter(children(node, "initializer_expression")), None)
            if init:
                return self.eval(init, env)
            if kind in ("initializer_expression", "collection_expression"):
                return [self.eval(n, env) for n in named]
            return []
        if kind == "object_creation_expression":
            typ = text(node.child_by_field_name("type"))
            args = self.args(node, env)
            init = next(iter(children(node, "initializer_expression")), None)
            simple = typ.split("<")[0].split("::")[-1].split(".")[-1]
            if "ReadOnly" in typ or simple in ("List", "HashSet", "IEnumerable"):
                return self.eval(init, env) if init else args[0] if len(args) == 1 and isinstance(args[0], list) else args
            if simple.endswith("Var") or simple == "PowerVar":
                if args and isinstance(args[0], str):
                    if len(args) == 1 and simple == "StringVar":
                        name, value = args[0], ""
                    elif len(args) == 1 and simple == "BoolVar":
                        name, value = args[0], False
                    elif len(args) == 1 and simple.startswith("Calculated"):
                        name, value = args[0], 0
                    elif len(args) > 1:
                        name, value = args[0], args[1]
                    else:
                        self.fail(node, "Unknown dynamic variable constructor")
                elif simple == "PowerVar":
                    name, value = re.search(r"PowerVar<([^>]+)>", typ)[1], args[0]
                elif simple.startswith("Calculated"):
                    name, value = simple[:-3], 0
                else:
                    name = simple[:-3]
                    try:
                        name = self.member("defaultName", simple)
                    except ExtractionError:
                        pass
                    value = args[0]
                return Var(name, value, simple.startswith("Calculated"))
            if simple in ("Decimal", "decimal"):
                return Decimal(args[0])
            if simple == "LocString":
                return LocValue(*args)
            self.fail(node, f"Unsupported constructor {typ}")
        if kind in ("lambda_expression", "anonymous_method_expression"):
            return node  # CalculatedVar multipliers are not evaluated outside combat.
        if kind == "invocation_expression":
            fn = node.child_by_field_name("function")
            name_node = fn.child_by_field_name("name") if fn else None
            name = text(name_node) if name_node else text(fn)
            simple = name.split("<")[0]
            args = self.args(node, env)
            obj_node = fn.child_by_field_name("expression") if fn else None
            obj_raw = text(obj_node)
            if obj_raw == "ModelDb" and "<" in name:
                model_name = name.split("<", 1)[1].rsplit(">", 1)[0]
                ns = "MegaCrit.Sts2.Core.Models." + simple + ("s" if simple != "AncientEvent" else "s")
                key = ns + "." + model_name
                return ForeignModel(key if key in self.index.types else model_name)
            if simple in ("GetFormattedText", "GetRawText"):
                obj = self.eval(obj_node, env)
                if isinstance(obj, LocValue):
                    from .formatting import render
                    try:
                        template = self.index.tables[obj.table][obj.key]
                    except KeyError:
                        raise ExtractionError(f"Missing localization key {obj.table}.{obj.key}")
                    return template if simple == "GetRawText" else render(template, obj.variables)
            if simple == "L10NMonsterLookup":
                return LocValue("monsters", args[0])
            if obj_raw == "SaveManager.Instance" and simple == "IsRelicSeen":
                # Export the fully revealed wiki title, independent of a save.
                return True
            if simple == "Add" and obj_node is not None:
                obj = self.eval(obj_node, env)
                if isinstance(obj, LocValue) and len(args) == 2:
                    obj.variables[args[0]] = args[1]
                    return None
            if simple in ("WithMultiplier", "FromOsty"):
                return self.eval(obj_node, env)
            if obj_raw == "AscensionHelper":
                if simple == "GetValueIfAscension":
                    return args[1] if self.ascension else args[2]
                if simple == "HasAscension":
                    return self.ascension
                if simple == "IsDread":
                    return False
            if obj_raw in ("Math", "MathF", "Mathf"):
                funcs = {"Min": min, "Max": max, "Abs": abs, "Floor": lambda v: int(v // 1), "Ceiling": lambda v: -int(-v // 1), "Round": round, "Clamp": lambda v, lo, hi: min(max(v, lo), hi)}
                if simple in funcs:
                    return funcs[simple](*args)
            if obj_raw == "Array" and simple == "Empty":
                return []
            if obj_raw == "Convert" and simple in ("ToInt32", "ToDecimal", "ToSingle"):
                return int(round(args[0])) if simple == "ToInt32" else Decimal(args[0])
            if simple in ("ToString", "ToLowerInvariant", "ToUpperInvariant") and obj_node:
                obj = self.eval(obj_node, env)
                value = obj.name.split(".")[-1] if isinstance(obj, Symbol) else str(obj)
                return value.lower() if simple == "ToLowerInvariant" else value.upper() if simple == "ToUpperInvariant" else value
            if simple in ("UpgradeValueBy", "UpgradeBy", "AddKeyword", "RemoveKeyword", "UpgradeStarCostBy"):
                if simple == "AddKeyword":
                    self.values["Keywords"].append(args[0])
                elif simple == "RemoveKeyword":
                    self.values["Keywords"] = [x for x in self.values["Keywords"] if x != args[0]]
                elif simple == "UpgradeStarCostBy":
                    self.values["BaseStarCost"] += args[0]
                else:
                    obj = self.eval(obj_node, env)
                    if isinstance(obj, Var):
                        obj.value += args[0]
                    elif isinstance(obj, dict) and "cost" in obj:
                        obj["cost"] += args[0]
                    else:
                        self.fail(node, "Unknown upgrade target")
                return None
            if simple in ("AssertMutable", "AssertCanonical"):
                return None
            if simple in ("ToList", "ToArray", "ToHashSet"):
                return list(self.eval(obj_node, env))
            if obj_node is None or obj_raw in ("base", "this"):
                typedef, methods = self.index.find(self.model.key if obj_raw != "base" else self.index.types[self.current_type].base, simple)
                return self.call(typedef, methods[0], args)
            self.fail(node, "Unsupported C# call")
        if kind == "conditional_expression":
            condition = self.eval(node.child_by_field_name("condition"), env)
            return self.eval(node.child_by_field_name("consequence") if condition else node.child_by_field_name("alternative"), env)
        if kind == "is_pattern_expression":
            obj = self.eval(node.child_by_field_name("expression"), env)
            pattern = node.child_by_field_name("pattern")
            declaration = next((p for p in walk(pattern) if p.type == "declaration_pattern"), None)
            if declaration:
                typ = text(declaration.child_by_field_name("type"))
                result = isinstance(obj, ForeignModel) and self.index.derives(obj.name, typ)
                if result:
                    designation = declaration.child_by_field_name("name") or declaration.named_children[-1]
                    env[text(designation)] = obj
                return not result if pattern.type == "negated_pattern" else result
            self.fail(node, "Unsupported C# pattern")
        if kind == "interpolated_string_expression":
            result = []
            for part in named:
                if part.type == "interpolation":
                    from .formatting import number
                    expression = next(n for n in part.named_children if n.type not in ("interpolation_brace", "interpolation_format_clause", "interpolation_alignment_clause"))
                    result.append(number(self.eval(expression, env)))
                elif part.type in ("string_content", "string_literal_fragment"):
                    result.append(text(part))
            return "".join(result)
        if kind == "binary_expression":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            op = text(node.child_by_field_name("operator")) or node.text[left.end_byte-node.start_byte:right.start_byte-node.start_byte].decode().strip()
            a = self.eval(left, env)
            if op == "&&": return bool(a) and bool(self.eval(right, env))
            if op == "||": return bool(a) or bool(self.eval(right, env))
            if op == "??": return a if a is not None else self.eval(right, env)
            b = self.eval(right, env)
            funcs = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv, "%": operator.mod, "==": operator.eq, "!=": operator.ne, ">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}
            if op in funcs:
                if op == "+" and isinstance(a, str): b = str(b)
                return funcs[op](a, b)
            if op in ("|", "&"):
                if isinstance(a, Symbol) and isinstance(b, Symbol):
                    return Symbol(a.name + op + b.name)
                return operator.or_(a, b) if op == "|" else operator.and_(a, b)
            self.fail(node)
        if kind == "prefix_unary_expression":
            value = self.eval(named[-1], env)
            return not value if raw.startswith("!") else -value if raw.startswith("-") else value
        if kind == "assignment_expression":
            left = node.child_by_field_name("left")
            value = self.eval(node.child_by_field_name("right"), env)
            key = text(left).removeprefix("base.").removeprefix("this.")
            if left.type == "element_access_expression":
                obj = self.eval(left.child_by_field_name("expression"), env)
                obj[self.args(left, env)[0]] = value
            elif "." not in key:
                env[key] = value
                self.values[key] = value
            else:
                self.fail(node, "Unsupported assignment")
            return value
        self.fail(node)

    def run(self, node, env):
        kind = node.type
        if kind == "block":
            for child in node.named_children:
                self.run(child, env)
        elif kind == "return_statement":
            raise Returned(self.eval(node.named_children[0], env) if node.named_children else None)
        elif kind == "local_declaration_statement":
            for declaration in children(node, "variable_declaration"):
                for var in children(declaration, "variable_declarator"):
                    name = text(var.child_by_field_name("name"))
                    value = next((c for c in var.named_children if c != var.child_by_field_name("name")), None)
                    if value: env[name] = self.eval(value, env)
        elif kind == "expression_statement":
            self.eval(node.named_children[0], env)
        elif kind == "if_statement":
            branch = node.child_by_field_name("consequence") if self.eval(node.child_by_field_name("condition"), env) else node.child_by_field_name("alternative")
            if branch: self.run(branch, env)
        elif kind in ("comment", "empty_statement"):
            return
        else:
            self.fail(node, "Unsupported C# statement")

    def run_member(self, node, env=None):
        env = {} if env is None else env
        arrow = next(iter(children(node, "arrow_expression_clause")), None)
        if arrow:
            return self.eval(arrow.named_children[0], env)
        block = node.child_by_field_name("body") or next(iter(children(node, "block")), None)
        if block is None:
            raise ExtractionError("C# member has no body")
        try:
            self.run(block, env)
        except Returned as ret:
            return ret.value

    def call(self, typedef, method, args):
        params = children(method.child_by_field_name("parameters"), "parameter")
        env = {text(p.child_by_field_name("name")): v for p, v in zip(params, args)}
        old = self.current_type
        self.current_type = typedef.key
        try:
            return self.run_member(method, env)
        finally:
            self.current_type = old

    def card_constructor(self):
        constructors = self.model.constructors
        constructor = next((c for c in constructors if not children(c.child_by_field_name("parameters"), "parameter")), None)
        if constructor is None:
            raise ExtractionError(f"No parameterless card constructor: {self.model.name}")
        init = next(iter(children(constructor, "constructor_initializer")), None)
        if init is None:
            raise ExtractionError(f"No card constructor initializer: {self.model.name}")
        args = self.args(init, {})
        if self.model.base != "CardModel":
            raise ExtractionError(f"Indirect card constructor: {self.model.name}")
        for name, value in zip(("CanonicalEnergyCost", "Type", "Rarity", "TargetType", "ShouldShowInCardLibrary"), args):
            self.values[name] = value
        self.values["EnergyCost"]["cost"] = args[0]
        self.values.setdefault("ShouldShowInCardLibrary", True)
        self.run_member(constructor)

    def initialize_vars(self):
        # Do not resolve through values: CanonicalVars is a property in the source.
        variables = self.optional("CanonicalVars", [])
        if not isinstance(variables, list) or not all(isinstance(x, Var) for x in variables):
            raise ExtractionError(f"Invalid CanonicalVars: {self.model.name}")
        self.values["DynamicVars"] = {v.name: v for v in variables}
        self.recalculate()

    def recalculate(self):
        variables = self.values["DynamicVars"]
        for var in variables.values():
            if var.calculated:
                base = variables.get("CalculationBase")
                if base is None:
                    raise ExtractionError(f"Missing CalculationBase for {var.name}")
                var.value = base.value

    def upgrade(self):
        try:
            typedef, methods = self.index.find(self.model.key, "OnUpgrade")
        except ExtractionError:
            return
        self.call(typedef, methods[0], [])
        self.recalculate()
