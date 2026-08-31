"""Keeps the prose in sync with the schema.

`EFFECT_VERBS` is the mechanical vocabulary. DESIGN-REVIEW decision 6 and the generation
prompt both enumerate it in English, and both have drifted from it before.
"""
import re
from pathlib import Path

from app.gen.prompts import GEN_SYSTEM
from app.models.effects import EFFECT_VERBS

DESIGN_REVIEW = Path(__file__).resolve().parents[1] / "DESIGN-REVIEW.md"

NUMBER_WORDS = {4: "four", 5: "five", 6: "six", 7: "seven"}


def _decision_six() -> str:
    """The decision 6 section: its heading through the next heading of any level."""
    lines = DESIGN_REVIEW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("### 6."))
    end = next(
        (i for i, line in enumerate(lines[start + 1:], start + 1)
         if line.startswith("## ") or line.startswith("### ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_design_review_lists_every_effect_verb():
    section = _decision_six()
    for verb in EFFECT_VERBS:
        assert f"`{verb}`" in section, f"decision 6 never names `{verb}`"

    expected = NUMBER_WORDS[len(EFFECT_VERBS)]
    assert f"{expected} effect verbs" in section
    for count, word in NUMBER_WORDS.items():
        if word == expected:
            continue
        wrong = re.search(rf"\b{word}\b(?:\s+effect)?\s+verbs", section)
        assert not wrong, f"decision 6 still miscounts the verbs: {wrong.group(0)!r}"


def test_generation_prompt_lists_every_effect_verb():
    for verb in EFFECT_VERBS:
        assert verb in GEN_SYSTEM, f"the generation prompt never names {verb}"
