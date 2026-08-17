from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_path: str = "token-crawl.sqlite3"
    log_level: str = "INFO"
    floors_dir: str = "floors"

    # --- LLM (OpenRouter) ---------------------------------------------------
    llm_backend: str = "openrouter"  # "openrouter" | "fixture"
    openrouter_api_key: str | None = None
    app_url: str = "https://github.com/lungdart/token-crawl"   # sent as HTTP-Referer
    app_title: str = "Token Crawl"                          # sent as X-Title

    # Tier A — parser/gatekeeper. High volume, latency-sensitive, tiny JSON out.
    # gemini-2.5-flash-lite: all 5 of its OpenRouter endpoints support structured
    # outputs (no endpoint roulette), it has no mandatory reasoning stage (lowest
    # latency), and it's ~$0.10/1k player actions. See scripts/models.py check.
    # Alternatives: openai/gpt-5.6-luna (reasoning:none), inclusionai/ling-2.6-flash (10x cheaper).
    parser_model: str = "google/gemini-2.5-flash-lite"

    # Tier B — world generation + adjudication. Quality-sensitive: this content is
    # cached forever and every future crawler sees it, so a whole 7x7 floor costs
    # well under $1 on any of these. Pick on prose, not price — run
    # `scripts/models.py compare` to judge the voice yourself.
    # Alternatives: z-ai/glm-5.2 (cheaper, punchier), moonshotai/kimi-k2.6 (best prose
    # reputation), x-ai/grok-4.3 (most irreverent), anthropic/claude-sonnet-5 (~$1.3/floor).
    gen_model: str = "google/gemini-2.5-flash"

    # Tier C — room art. The model draws a full-colour picture; code shrinks it to
    # 64x48 and quantizes it to sixteen colours chosen from that picture, with
    # Floyd-Steinberg dithering. Asking a text model to emit the pixels directly does
    # not work at this size — see scripts/art_bakeoff.py for the comparison that
    # settled it. ~$0.03 a room, paid once ever.
    image_model: str = "google/gemini-2.5-flash-image"

    gen_max_tokens: int = 4000
    parser_max_tokens: int = 400
    request_timeout_s: float = 90.0
    max_generation_attempts: int = 2

    # --- Security / cost controls -------------------------------------------
    max_action_chars: int = 300
    max_concept_chars: int = 200
    # Rate limits live in app/security/limits.py; they are anti-bot constants,
    # not things to tune per deployment.


settings = Settings()
