"""Pydantic -> OpenRouter/OpenAI strict JSON Schema.

Strict structured-output mode accepts only a subset of JSON Schema. Pydantic v2
emits plenty that is outside it, so we rewrite:

  * `oneOf` (discriminated unions)  -> `anyOf`, and drop the `discriminator` block
  * `const: X`                      -> `enum: [X]`
  * every object gets `additionalProperties: false`
  * EVERY property must appear in `required` (strict mode has no optionals),
    so defaulted fields become required and their `default` is dropped
  * validation keywords the strict subset rejects (`pattern`, `maxLength`,
    `minimum`, `maxItems`, `format`, ...) are stripped

Stripping constraints would lose real information the model needs, so each one is
folded into that field's `description` as plain English instead. The engine still
enforces them for real: every response is validated against the untouched Pydantic
model, and violations trigger a retry with feedback.

`$defs`/`$ref` are preserved rather than inlined — the strict subset supports them,
and the Effect union is recursive (OnHitTrigger contains an Effect), so inlining
would not terminate.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Numeric/array bounds are supported by BOTH the OpenAI and Gemini strict subsets,
# so we keep them in the schema — that gets the floor's power budget enforced during
# constrained decoding rather than only caught on the validation retry. They are also
# echoed into `description` for providers that treat the schema as a strong hint.
_KEEP_WITH_PHRASE: dict[str, str] = {
    "minimum": "minimum {v}",
    "maximum": "maximum {v}",
    "minItems": "at least {v} items",
    "maxItems": "at most {v} items",
}

# Not portable across strict implementations (OpenAI rejects some, Gemini documents
# neither). Stripped from the schema, preserved as prose so the model still complies.
_STRIP_WITH_PHRASE: dict[str, str] = {
    "pattern": "must match regex {v}",
    "minLength": "at least {v} characters",
    "maxLength": "at most {v} characters",
    "exclusiveMinimum": "greater than {v}",
    "exclusiveMaximum": "less than {v}",
    "multipleOf": "a multiple of {v}",
    "format": "format: {v}",
}

# Dropped outright: unsupported, meaningless once everything is required, or pure noise.
_DROP_KEYS = {"default", "discriminator", "prefixItems", "uniqueItems", "examples", "title"}


MAX_DESCRIPTION = 180


def _describe_constraints(node: dict[str, Any]) -> str:
    """Only STRIPPED constraints need prose — kept ones are still in the schema, and
    duplicating them inflates the constrained-decoding state space. Providers reject a
    schema outright once it has 'too many states for serving'."""
    parts = []
    for key, phrase in _STRIP_WITH_PHRASE.items():
        if key in node:
            parts.append(phrase.format(v=node[key]))
    return "; ".join(parts)


def _flatten_allof(node: dict[str, Any]) -> dict[str, Any]:
    """Pydantic emits `allOf` when a $ref carries siblings (e.g. a description).
    OpenAI's strict subset rejects allOf, so merge it into the parent."""
    subschemas = node.get("allOf")
    if not isinstance(subschemas, list) or len(subschemas) != 1:
        return node
    merged = {k: v for k, v in node.items() if k != "allOf"}
    for key, value in subschemas[0].items():
        merged.setdefault(key, value)
    return merged


def _walk_map(node: Any) -> Any:
    """Walk a map of NAME -> schema (`properties`, `$defs`). Keys here are user field
    names, not JSON Schema keywords — a field called `description` or `pattern` must not
    be mistaken for one."""
    if not isinstance(node, dict):
        return _walk(node)
    return {name: _walk(schema) for name, schema in node.items()}


def _walk(node: Any) -> Any:
    if isinstance(node, list):
        return [_walk(n) for n in node]
    if not isinstance(node, dict):
        return node

    node = _flatten_allof(node)
    out: dict[str, Any] = {}
    constraint_text = _describe_constraints(node)

    for key, value in node.items():
        if key in _DROP_KEYS or key in _STRIP_WITH_PHRASE:
            continue
        if key in ("properties", "$defs", "definitions"):
            out[key] = _walk_map(value)
            continue
        if key == "oneOf":  # discriminated unions -> anyOf
            out["anyOf"] = _walk(value)
            continue
        if key == "const":  # Literal[...] -> single-value enum
            out["enum"] = [value]
            out.setdefault("type", _json_type_of(value))
            continue
        out[key] = _walk(value)

    # Preserve the dropped constraints as instructions the model can actually read.
    if constraint_text:
        existing = out.get("description", "")
        out["description"] = f"{existing} ({constraint_text})".strip() if existing else constraint_text
    if isinstance(out.get("description"), str):
        out["description"] = out["description"][:MAX_DESCRIPTION]

    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties", {})
        out["additionalProperties"] = False
        # Strict mode has no optional properties: everything is required.
        out["required"] = list(props.keys())

    return out


def _json_type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def to_strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Strict-mode JSON Schema for a Pydantic model."""
    return _walk(model.model_json_schema())


def response_format(model: type[BaseModel], name: str) -> dict[str, Any]:
    """The `response_format` block for an OpenRouter chat-completions request."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": to_strict_schema(model),
        },
    }
