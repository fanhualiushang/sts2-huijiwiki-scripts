"""The game's localization formatters, with a non-combat display context."""
from __future__ import annotations

import re
from decimal import Decimal

from .csharp import ExtractionError, Symbol, Var


def number(value):
    if isinstance(value, Var):
        value = value.value
    if isinstance(value, Symbol):
        return value.name.split(".")[-1]
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        return str(int(value)) if value == int(value) else str(value)
    return str(value)


def split_top(value, separator, limit=-1):
    result, start, braces, parens = [], 0, 0, 0
    for i, char in enumerate(value):
        if char == "{": braces += 1
        elif char == "}": braces -= 1
        elif char == "(": parens += 1
        elif char == ")": parens -= 1
        elif char == separator and braces == 0 and parens == 0 and (limit < 0 or len(result) < limit):
            result.append(value[start:i])
            start = i + 1
    result.append(value[start:])
    return result


def render(template, variables):
    def lookup(key):
        key = key.strip()
        bits = key.split(".")
        if bits[0] not in variables:
            raise ExtractionError(f"Missing localization variable {{{key}}}")
        value = variables[bits[0]]
        if callable(value):
            value = value()
        for bit in bits[1:]:
            if isinstance(value, Var) and bit in ("BaseValue", "IntValue", "PreviewValue", "EnchantedValue", "StringValue", "BoolVal"):
                value = int(value.value) if bit == "IntValue" else value.value
            elif isinstance(value, dict) and bit in value:
                value = value[bit]
            else:
                raise ExtractionError(f"Unsupported localization selector {key}")
        return value

    def condition(test, value):
        match = re.fullmatch(r"\s*(>=|<=|!=|==|=|>|<)?\s*(-?\d+(?:\.\d+)?)\s*", test)
        if not match:
            raise ExtractionError(f"Unknown localization condition {test}")
        rhs = Decimal(match[2])
        op = match[1] or "="
        if value == "X":
            if rhs != 0:
                raise ExtractionError("A symbolic stack amount cannot select a numeric threshold")
            value = 1  # Generic positive-stack wording; the displayed amount remains X.
        return {">=": lambda: value >= rhs, "<=": lambda: value <= rhs,
                "!=": lambda: value != rhs, "==": lambda: value == rhs,
                "=": lambda: value == rhs, ">": lambda: value > rhs, "<": lambda: value < rhs}[op]()

    def placeholder(body):
        parts = split_top(body, ":", 2)
        value = lookup(parts[0])
        scalar = value.value if isinstance(value, Var) else value
        if len(parts) == 1:
            return number(value)
        fmt, options, content = "", "", ""
        match = re.fullmatch(r"(\w+)(?:\((.*?)\))?", parts[1], re.S)
        known = {"diff", "inverseDiff", "energyIcons", "starIcons", "abs", "percentMore", "percentLess", "show", "choose", "cond", "plural", "list", "number"}
        if match and match[1] in known:
            fmt, options = match[1], match[2] or ""
            content = parts[2] if len(parts) > 2 else ""
        else:
            content = ":".join(parts[1:])
        if fmt in ("diff", "inverseDiff", "number"):
            return "X" if scalar == "X" else number(int(scalar))
        if fmt == "abs":
            return "X" if scalar == "X" else number(abs(scalar))
        if fmt in ("percentMore", "percentLess"):
            if scalar == "X": return "X"
            percent = (scalar - 1) * 100 if fmt == "percentMore" else (1 - scalar) * 100
            return number(int(round(percent)))
        if fmt in ("energyIcons", "starIcons"):
            amount = int(options) if isinstance(scalar, str) and scalar != "X" else scalar
            tag = "E" if fmt == "energyIcons" else "STAR"
            if amount == "X": return "X[" + tag + "]"
            amount = int(amount)
            return ("[" + tag + "]") * amount if 0 < amount < 5 else str(amount) + "[" + tag + "]"
        choices = split_top(content, "|")
        if fmt == "show":
            choice = choices[0] if bool(scalar) else choices[1] if len(choices) > 1 else ""
        elif fmt == "choose":
            labels = options.split("|")
            key = number(scalar)
            idx = labels.index(key) if key in labels else len(labels)
            if idx >= len(choices): raise ExtractionError(f"Missing choose branch for {key}")
            choice = choices[idx]
        elif fmt == "plural":
            # Chinese has one plural category. The final branch is the generic one.
            choice = choices[-1]
        elif fmt == "cond":
            if isinstance(scalar, (str, bool)) and len(choices) == 2 and not any(len(split_top(c, "?", 1)) == 2 for c in choices):
                choice = choices[0] if scalar else choices[1]
            else:
                choice = choices[-1]
                for item in choices:
                    pair = split_top(item, "?", 1)
                    if len(pair) == 2 and condition(pair[0], scalar):
                        choice = pair[1]
                        break
        elif not fmt and isinstance(scalar, bool):
            choice = choices[0] if scalar else choices[1] if len(choices) > 1 else ""
        elif not fmt and len(choices) > 1:
            if isinstance(scalar, bool):
                idx = 0 if scalar else 1
            elif isinstance(scalar, (int, Decimal)):
                idx = min(max(int(scalar), 0), len(choices) - 1)
            else:
                raise ExtractionError(f"Unsupported implicit conditional {body}")
            choice = choices[idx]
        elif fmt == "list":
            return ", ".join(number(x) for x in scalar)
        elif not fmt and not content:
            return number(scalar)
        else:
            raise ExtractionError(f"Unsupported localization format {{{body}}}")
        return render(choice, {**variables, "": value})

    out, pos = [], 0
    while pos < len(template):
        if template[pos] != "{":
            out.append(template[pos])
            pos += 1
            continue
        start, depth = pos + 1, 1
        pos += 1
        while pos < len(template) and depth:
            if template[pos] == "{": depth += 1
            elif template[pos] == "}": depth -= 1
            pos += 1
        if depth:
            raise ExtractionError(f"Unclosed localization placeholder: {template[start-1:]}")
        out.append(placeholder(template[start:pos-1]))
    return "".join(out)
