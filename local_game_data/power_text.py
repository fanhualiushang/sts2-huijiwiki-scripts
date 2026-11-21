"""Render stack-dependent wiki descriptions without inventing a stack amount."""
import re

from .csharp import ExtractionError
from .formatting import render


def reconcile_description(game, reference, generated):
    old_text = reference.get("description") or ""
    template = game.get("smart_description_template")
    # The formal table explicitly uses X for these stack/turn counts. Keep
    # that notation while taking wording and fixed constants from this version.
    symbolic = "X" in old_text
    mode = "source_smart_description"
    amount = "X"
    generic_template = game.get("description_template") or ""
    if not symbolic:
        if not re.search(r'\{Amount(?:\:[^{}]*)?\}', generic_template):
            return generated, None
        if game.get("default_application_amount") is not None:
            amount = game["default_application_amount"]
            mode = "source_application_amount"
        elif old_text:
            return old_text, {"mode": "formal_application_amount_fallback", "reason": "The canonical power's Amount is not an applied amount"}
        template = generic_template
    if not template:
        return generated, None
    values = {**game.get("description_variables", {}), "Amount": amount, "InCombat": False,
              "OwnerName": "该生物", "ApplierName": "施加者", "TargetName": "目标",
              "OnPlayer": False, "IsMultiplayer": False, "PlayerCount": 1,
              "energyPrefix": "colorless", "singleStarIcon": "[STAR]"}
    try:
        omitted = []
        for variable in game.get("runtime_description_variables", []):
            if values.get(variable) != 0: continue
            # Inferno/Crimson Mantle initialise SelfDamage at 0, and their cards
            # increment it after applying the power. Zero is not a real display
            # amount. Keep the generic "lose HP" wording rather than guessing
            # how many copies were played or conflating it with Amount's X.
            pattern = r'\[blue\]\{' + re.escape(variable) + r'(?:\:[^{}]*)?\}\[/blue\]点'
            updated, count = re.subn(pattern, "", template)
            if count:
                template = updated
                omitted.append(variable)
        rendered = render(template, values)
        # The wiki uses a coloured X even where the game omits number colour.
        rendered = re.sub(r"(?<!\])X(?!\[)", "[blue]X[/blue]", rendered)
        from tag_parser import parse_tag
        result = parse_tag(rendered.replace("\n", "<br>"), "colorless")
        result = result.replace("[E]", "[[File:colorless_energy_icon.png|16px|link=能量]]")
        result = result.replace("[STAR]", "[[File:star_icon.png|16px|link=辉星]]")
        return result, {"mode": mode, "amount": str(amount), "application_source": game.get("_application_amount_source"), "omitted_runtime_parameters": omitted}
    except (ExtractionError, ValueError, TypeError, KeyError) as exc:
        # No player/run state is available for SelfDamage, CardsLeft, etc.
        # Preserve the known formal wording instead of replacing it with 0.
        return old_text, {"mode": "formal_description_fallback", "reason": str(exc)}
