"""OpenRouter backend: request shape and every retry path, over mocked HTTP."""
import json

import httpx
import pytest
import respx

from app import db
from app.gen import llm
from app.gen.llm import OpenRouterBackend, extract_json
from app.gen.schema import to_strict_schema
from app.models.entities import EnemyStatBlock

URL = "https://openrouter.ai/api/v1/chat/completions"

GOOD_ENEMY = {
    "name": "Shank Goblin", "flavor": "Stabby.", "hp": 12, "attack": 3, "defense": 1,
    "speed": 4, "xp": 15, "gold": "1d6", "attack_dice": "1d4", "abilities": [],
}


def _completion(content, finish_reason="stop", cost=0.0012, cached=0):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": 1500, "completion_tokens": 200, "cost": cost,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


@pytest.fixture()
def backend(game):
    return OpenRouterBackend(api_key="test-key")


def _gen(backend, **kw):
    defaults = dict(
        kind="enemy", model="test/model",
        system_blocks=[{"type": "text", "text": "sys"},
                       {"type": "text", "text": "floor brief", "cache_control": {"type": "ephemeral"}}],
        user="make an enemy", output_model=EnemyStatBlock,
    )
    return backend.generate(**{**defaults, **kw})


@respx.mock
def test_request_shape_and_success(backend):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_completion(json.dumps(GOOD_ENEMY))))
    obj = _gen(backend)
    assert obj.name == "Shank Goblin"

    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "test/model"
    # strict structured outputs
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    # only route to providers that honor response_format
    assert body["provider"]["require_parameters"] is True
    # ask for real cost back
    assert body["usage"]["include"] is True
    # cache breakpoint on the static floor brief survives into the system message
    assert body["messages"][0]["content"][1]["cache_control"] == {"type": "ephemeral"}

    headers = route.calls[0].request.headers
    assert headers["authorization"] == "Bearer test-key"
    assert "x-title" in headers


@respx.mock
def test_cost_and_cache_logged(backend):
    respx.post(URL).mock(return_value=httpx.Response(
        200, json=_completion(json.dumps(GOOD_ENEMY), cost=0.0042, cached=1400)))
    _gen(backend)
    row = db.get().execute(
        "SELECT cost_usd, cache_read_tokens, input_tokens, ok FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["cost_usd"] == pytest.approx(0.0042)
    assert row["cache_read_tokens"] == 1400
    assert row["ok"] == 1


@respx.mock
def test_markdown_fenced_json_recovered(backend):
    fenced = f"```json\n{json.dumps(GOOD_ENEMY)}\n```"
    respx.post(URL).mock(return_value=httpx.Response(200, json=_completion(fenced)))
    assert _gen(backend).name == "Shank Goblin"


@respx.mock
def test_schema_violation_retries_with_feedback(backend):
    bad = json.dumps({"name": "Broken"})  # missing required fields
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(200, json=_completion(bad)),
        httpx.Response(200, json=_completion(json.dumps(GOOD_ENEMY))),
    ])
    assert _gen(backend).name == "Shank Goblin"
    assert route.call_count == 2
    retry_body = json.loads(route.calls[1].request.content)
    assert "failed validation" in retry_body["messages"][-1]["content"]


@respx.mock
def test_truncation_retries(backend):
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(200, json=_completion('{"name": "cut o', finish_reason="length")),
        httpx.Response(200, json=_completion(json.dumps(GOOD_ENEMY))),
    ])
    assert _gen(backend).name == "Shank Goblin"
    assert route.call_count == 2


@respx.mock
def test_client_error_raises_immediately(backend):
    route = respx.post(URL).mock(return_value=httpx.Response(
        400, json={"error": {"message": "no endpoints support structured outputs"}}))
    with pytest.raises(llm.GenerationError, match="400"):
        _gen(backend)
    assert route.call_count == 1  # 4xx won't fix itself; don't burn a retry


@respx.mock
def test_rate_limit_is_retried(backend):
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(429, json={"error": {"message": "rate limited"}}),
        httpx.Response(200, json=_completion(json.dumps(GOOD_ENEMY))),
    ])
    assert _gen(backend).name == "Shank Goblin"
    assert route.call_count == 2


@respx.mock
def test_exhausted_retries_raise(backend):
    respx.post(URL).mock(return_value=httpx.Response(200, json=_completion("not json at all")))
    with pytest.raises(llm.GenerationError):
        _gen(backend)


def test_missing_api_key_is_a_clear_error(game, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    with pytest.raises(llm.GenerationError, match="OPENROUTER_API_KEY"):
        OpenRouterBackend()


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}


def test_strict_schema_has_no_unsupported_keywords():
    from app.models.actions import ParsedAction
    from app.models.character import Ability, CrawlerClass, LevelUp
    from app.models.entities import AreaContent, DropTable, Item, SafeRoom
    from app.models.responses import ResponseBank
    from app.models.rulings import InteractionRuling

    models = (EnemyStatBlock, AreaContent, Item, DropTable, SafeRoom, ResponseBank,
              ParsedAction, InteractionRuling, CrawlerClass, Ability, LevelUp)
    for model in models:
        text = json.dumps(to_strict_schema(model))
        for keyword in ("oneOf", "allOf", "discriminator", "pattern", "maxLength",
                        "minLength", "default", "const", "title", "prefixItems"):
            assert f'"{keyword}"' not in text, f"{model.__name__} still emits {keyword}"


def test_strict_schema_keeps_portable_numeric_bounds():
    """minimum/maximum are supported by both OpenAI and Gemini strict modes, so they stay
    in the schema and are enforced during decoding. They are NOT echoed into descriptions —
    duplicating them inflates the decoder state space, and providers reject a schema outright
    once it has 'too many states for serving'."""
    hp = to_strict_schema(EnemyStatBlock)["properties"]["hp"]
    assert hp["minimum"] == 1 and hp["maximum"] == 100000
    assert "minimum" not in hp.get("description", "")


def test_field_named_like_a_keyword_is_not_mangled():
    """`properties` is a map of user field names, not schema keywords — a field called
    `description` must survive as its own sub-schema."""
    from app.models.entities import AreaContent

    props = to_strict_schema(AreaContent)["properties"]
    assert props["description"]["type"] == "string"
    assert "description" in to_strict_schema(AreaContent)["required"]


def test_stripped_constraints_survive_as_prose():
    """The dice regex is unportable as `pattern`, so it must reach the model in words."""
    desc = to_strict_schema(EnemyStatBlock)["properties"]["attack_dice"]["description"]
    assert "must match regex" in desc and "d" in desc


def test_root_is_an_object_not_a_union():
    """OpenAI strict mode rejects a root-level anyOf; the 14-way action union must stay
    wrapped in an object."""
    from app.models.actions import ParsedAction

    schema = to_strict_schema(ParsedAction)
    assert schema["type"] == "object"
    assert "anyOf" not in schema
    assert schema["required"] == ["action"]


def test_allof_is_flattened():
    from pydantic import BaseModel, Field

    class Inner(BaseModel):
        x: int

    class Outer(BaseModel):
        inner: Inner = Field(description="a described ref, which makes Pydantic emit allOf")

    text = json.dumps(to_strict_schema(Outer))
    assert '"allOf"' not in text


def test_strict_schema_requires_every_property():
    schema = to_strict_schema(EnemyStatBlock)
    # `gold` has a default in Python but must still be required for strict mode
    assert set(schema["required"]) == set(schema["properties"].keys())
    assert "gold" in schema["required"]


def test_the_connection_is_closed_on_shutdown(monkeypatch):
    """It used to be opened once and never closed. Nothing broke, but tests swapping
    backends left connections open behind them."""
    import httpx

    from app.gen import llm as llm_mod

    monkeypatch.setattr(llm_mod.settings, "openrouter_api_key", "sk-test")
    client = httpx.Client()
    backend = llm_mod.OpenRouterBackend(client=client)

    backend.close()
    assert not client.is_closed, "a client we were handed is not ours to close"

    own = llm_mod.OpenRouterBackend()
    inner = own.client
    own.close()
    assert inner.is_closed


def test_replacing_a_backend_closes_the_old_one(monkeypatch):
    from app.gen import llm as llm_mod

    monkeypatch.setattr(llm_mod.settings, "openrouter_api_key", "sk-test")
    first = llm_mod.OpenRouterBackend()
    llm_mod.set_backend(first)
    llm_mod.set_backend(None)
    assert first.client.is_closed
