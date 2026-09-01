"""Keeps the prose in sync with the schema.

`EFFECT_VERBS` is the mechanical vocabulary. DESIGN-REVIEW decision 6 and the generation
prompt both enumerate it in English, and both have drifted from it before.
"""
import inspect
import itertools
import re
import types
import typing
from pathlib import Path

from app.gen import prompts
from app.gen.prompts import GEN_SYSTEM
from app.models.effects import EFFECT_VERBS
from app.models.floor_brief import FloorBrief

DESIGN_REVIEW = Path(__file__).resolve().parents[1] / "DESIGN-REVIEW.md"

NUMBER_WORDS = {4: "four", 5: "five", 6: "six", 7: "seven"}

VERB_COUNT_RE = re.compile(rf"\b({'|'.join(NUMBER_WORDS.values())})\b(?:\s+effect)?\s+verbs")


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


SAMPLES = {
    bool: (True, False),
    int: (1,),
    float: (0.5,),
    str: ("x",),
    dict: ({},),
    list: ([],),
    type(None): (None,),
    FloorBrief: (FloorBrief(floor=1, slug="a-floor", title="A Floor"),),
}


def _samples(annotation) -> tuple:
    """Values to render a parameter with — every branch, for anything that has two."""
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        return tuple(v for arg in typing.get_args(annotation) for v in _samples(arg))
    origin = typing.get_origin(annotation) or annotation
    assert origin in SAMPLES, f"no sample value for {annotation!r}; add one to SAMPLES"
    return SAMPLES[origin]


def _text(rendered) -> str:
    """Builders return either the prompt or a list of system blocks."""
    if isinstance(rendered, str):
        return rendered
    return "\n".join(block["text"] for block in rendered)


def _prompt_texts() -> dict[str, str]:
    """Every string this module hands a model, keyed by where it came from.

    Constants and rendered builder returns both, because the count is written into prose in
    either. Reading the source instead would miss a sentence split across two literals mid-
    phrase, which is where one of them already sat.
    """
    texts = {}
    for name, obj in vars(prompts).items():
        if name.startswith("_"):
            continue
        if isinstance(obj, str):
            texts[name] = obj
        elif inspect.isfunction(obj) and obj.__module__ == prompts.__name__:
            params = inspect.signature(obj).parameters
            options = [[(p, v) for v in _samples(param.annotation)] for p, param in params.items()]
            for combo in itertools.product(*options):
                kwargs = dict(combo)
                texts[f"{name}({kwargs})"] = _text(obj(**kwargs))
    return texts


def test_prompts_count_the_effect_verbs_correctly():
    """A prompt that tells the model it has six verbs when the engine has seven is a lie the
    model will believe — it stops composing with the one it was never told about."""
    expected = NUMBER_WORDS[len(EFFECT_VERBS)]
    wrong = []
    for where, text in _prompt_texts().items():
        for hit in VERB_COUNT_RE.finditer(text):
            if hit.group(1) != expected:
                wrong.append(f"{where}: {hit.group(0)!r}")

    assert not wrong, (
        f"the engine has {len(EFFECT_VERBS)} effect verbs; these prompts say otherwise:\n"
        + "\n".join(wrong)
    )
