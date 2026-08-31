"""Prompt prose has to agree with the schema it will be validated against.

Asking for more than the schema accepts costs a refusal from constrained decoding or a
failed validation and a retry, so these read the bounds off the model rather than
hardcoding them — tightening a field fails here until the prompt follows it down.
"""
import annotated_types

from app.gen import prompts
from app.models.character import CrawlerClass


def _bounds(field: str) -> tuple[int, int]:
    lo, hi = 0, 0
    for c in CrawlerClass.model_fields[field].metadata:
        if isinstance(c, annotated_types.MinLen):
            lo = c.min_length
        elif isinstance(c, annotated_types.MaxLen):
            hi = c.max_length
    return lo, hi


def test_class_prompt_asks_for_counts_the_schema_accepts():
    text = prompts.class_prompt("a tired knight")

    ab_lo, ab_hi = _bounds("starting_abilities")
    assert f"{ab_lo}-{ab_hi} starting abilities" in text

    it_lo, it_hi = _bounds("starting_items")
    assert f"{it_lo}-{it_hi} starting item briefs" in text
