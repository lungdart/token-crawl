"""Every model tier costs money, so every model tier has to be documented.

A new `*_model` setting in app/config.py is a new line on someone's OpenRouter bill.
This fails until README.md and .env.example both name its env var and agree on how
many tiers there are.
"""
import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
COUNT_WORDS = {2: "two", 3: "three", 4: "four"}


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
