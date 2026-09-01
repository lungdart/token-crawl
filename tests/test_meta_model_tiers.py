"""Every model tier costs money, so every model tier has to be documented.

A new `*_model` setting in app/config.py is a new line on someone's OpenRouter bill.
This fails until README.md and .env.example both name its env var and agree on how
many tiers there are.

And prose in a comment is never executed, so a model name in one rots silently: the
tier gets repointed and the comment keeps sending the next reader to a model that
isn't in the build. So comments name the tier, not the vendor.
"""
import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
COUNT_WORDS = {2: "two", 3: "three", 4: "four"}

# Model families someone might reach for when writing "let X disambiguate".
FAMILIES = ("haiku", "sonnet", "opus", "gpt", "gemini", "grok", "kimi", "glm",
            "ling", "deepseek")
FAMILY_WORD = re.compile(r"\b(" + "|".join(FAMILIES) + r")\b", re.IGNORECASE)
# `vendor/model-1.2` — a full slug is a pin, not prose, so it is allowed to name
# whatever it likes (config.py lists alternatives that way).
SLUG = re.compile(r"[\w.-]+/[\w.-]+")


def test_every_model_setting_is_documented():
    settings = [name for name in Settings.model_fields if name.endswith("_model")]
    docs = {name: (ROOT / name).read_text() for name in ("README.md", ".env.example")}

    for setting in settings:
        env_var = setting.upper()
        for name, text in docs.items():
            assert env_var in text, f"{env_var} is not documented in {name}"

    want = COUNT_WORDS[len(settings)]
    for name, text in docs.items():
        said = re.search(r"(\w+) (?:model )?tiers", text)
        assert said, f"{name} never says how many model tiers there are"
        assert said.group(1) == want, (
            f"{name} says {said.group(1)!r} tiers, but Settings has {len(settings)}"
        )


def test_source_only_names_models_that_are_configured():
    configured = " ".join(
        str(field.default).lower()
        for name, field in Settings.model_fields.items()
        if name.endswith("_model")
    )
    stale = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for match in FAMILY_WORD.finditer(SLUG.sub(" ", line)):
                word = match.group(1)
                if word.lower() in configured:
                    continue
                stale.append(f"{path.relative_to(ROOT)}:{lineno} -> {word}")

    assert not stale, (
        "source names a model family that no *_model setting uses; name the tier "
        "(\"the parser tier\", \"the parser model\"), not the vendor:\n" + "\n".join(stale)
    )
