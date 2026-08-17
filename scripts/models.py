#!/usr/bin/env python
"""Pick and verify the two model tiers.

    uv run scripts/models.py check                 # per-endpoint structured-output support
    uv run scripts/models.py check --all           # ...for the whole candidate shortlist
    uv run scripts/models.py compare               # generate the same area with each candidate
    uv run scripts/models.py compare --kind enemy  # ...or an enemy stat block

`check` matters because OpenRouter reports structured-output support as a *union*
across providers on /models, but routes per endpoint. An endpoint that doesn't
support response_format will silently ignore it and hand back prose. Anything with
a `false` row below can still be routed to unless `provider.require_parameters` is
set — which this app always sets.

`compare` exists because prose quality is the one thing you can't look up. It runs
the real generation prompt through each candidate against a scratch database, so
nothing touches the shared world cache.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

CANDIDATES = {
    "parser": [
        "google/gemini-2.5-flash-lite",
        "openai/gpt-5.6-luna",
        "inclusionai/ling-2.6-flash",
        "deepseek/deepseek-v4-flash",
    ],
    "gen": [
        "google/gemini-2.5-flash",
        "z-ai/glm-5.2",
        "moonshotai/kimi-k2.6",
        "x-ai/grok-4.3",
        "google/gemini-3.7-flash",
        "anthropic/claude-sonnet-5",
    ],
}


def check(slugs: list[str]) -> None:
    print(f"{'model':38} {'endpoints':>9} {'with SO':>8}  providers missing structured outputs")
    print("-" * 110)
    with httpx.Client(timeout=30) as client:
        for slug in slugs:
            try:
                r = client.get(f"https://openrouter.ai/api/v1/models/{slug}/endpoints")
                if r.status_code != 200:
                    print(f"{slug:38} {'?':>9} {'?':>8}  HTTP {r.status_code}")
                    continue
                eps = r.json()["data"]["endpoints"]
            except Exception as exc:
                print(f"{slug:38} {'?':>9} {'?':>8}  {exc}")
                continue
            ok, bad = [], []
            for e in eps:
                (ok if "structured_outputs" in (e.get("supported_parameters") or []) else bad).append(
                    e.get("provider_name", "?")
                )
            flag = "" if not bad else "  <-- " + ", ".join(bad)
            print(f"{slug:38} {len(eps):>9} {len(ok):>8}{flag}")
    print("\nAll endpoints supporting structured outputs = no routing roulette.")
    print("This app always sends provider.require_parameters=true, so unsupported")
    print("endpoints are excluded — but a model whose *every* endpoint is false will fail outright.")


def compare(slugs: list[str], kind: str) -> None:
    import tempfile

    from app import db
    from app.config import settings
    from app.gen import llm, prompts
    from app.models.entities import AreaContent, EnemyStatBlock
    from app.world import floors

    with tempfile.TemporaryDirectory() as tmp:
        db.init(str(Path(tmp) / "compare.sqlite3"))
        floors.load_floors(settings.floors_dir)
        brief = floors.get_brief(1)
        backend = llm.OpenRouterBackend()

        if kind == "area":
            model_cls = AreaContent
            user = prompts.area_prompt(
                3, 3, is_landing=True, has_stairs=False, is_shop=False, dist_from_landing=0,
                neighbors={}, forced_exits={}, blocked_dirs=[],
                enemy_density=brief.target_enemy_density,
            )
            validate = None
        else:
            model_cls = EnemyStatBlock
            user = prompts.enemy_prompt("shank_goblin", "Shank Goblin", "The Collapsed Junction")
            b = brief.power_budget

            def validate(block):
                errs = []
                for stat, (lo, hi) in (("hp", b.enemy_hp), ("attack", b.enemy_attack),
                                       ("xp", b.enemy_xp)):
                    v = getattr(block, stat)
                    if not (lo <= v <= hi):
                        errs.append(f"{stat}={v} outside [{lo},{hi}]")
                return errs

        for slug in slugs:
            print("\n" + "=" * 100)
            print(f"MODEL: {slug}")
            print("=" * 100)
            before = _spend()
            start = time.monotonic()
            try:
                obj = backend.generate(
                    kind=kind, model=slug,
                    system_blocks=prompts.floor_prefix(brief),
                    user=user, output_model=model_cls, validate=validate,
                    session_id="compare",
                )
            except Exception as exc:
                print(f"  FAILED: {exc}")
                continue
            elapsed = time.monotonic() - start
            attempts = _attempts(slug)
            print(f"  ${_spend() - before:.5f}   {elapsed:5.1f}s   {attempts} call(s) "
                  f"{'(needed a retry)' if attempts > 1 else ''}\n")
            if kind == "area":
                print(f"  NAME: {obj.name}")
                print(f"  {obj.description}\n")
                for e in obj.entities:
                    print(f"    [{e.kind}] {e.name} ({e.key}) — {e.brief}")
            else:
                print(f"  {obj.name}: hp={obj.hp} atk={obj.attack} def={obj.defense} "
                      f"spd={obj.speed} xp={obj.xp} dice={obj.attack_dice}")
                print(f"  {obj.flavor}")
                for a in obj.abilities:
                    print(f"    ✦ {a.name} — {a.flavor}")

        print("\n" + "-" * 100)
        print("Totals per model (cost, calls, failures):")
        for row in db.get().execute(
            "SELECT model, ROUND(SUM(cost_usd), 5) usd, COUNT(*) n, SUM(1-ok) failed "
            "FROM llm_calls GROUP BY model ORDER BY usd DESC"
        ):
            print(f"  {row['model']:38} ${row['usd'] or 0:<9} {row['n']} call(s), {row['failed']} rejected")


def _spend() -> float:
    from app import db
    return db.get().execute("SELECT COALESCE(SUM(cost_usd),0) c FROM llm_calls").fetchone()["c"]


def _attempts(model: str) -> int:
    from app import db
    return db.get().execute(
        "SELECT COUNT(*) c FROM llm_calls WHERE model=?", (model,)
    ).fetchone()["c"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="per-endpoint structured-output support (no API key needed)")
    c.add_argument("--all", action="store_true", help="check the full candidate shortlist")
    c.add_argument("models", nargs="*", help="specific slugs to check")

    p = sub.add_parser("compare", help="generate the same content with each candidate (costs money)")
    p.add_argument("--kind", choices=["area", "enemy"], default="area")
    p.add_argument("--tier", choices=["parser", "gen"], default="gen")
    p.add_argument("models", nargs="*", help="specific slugs to compare")

    args = ap.parse_args()
    if args.cmd == "check":
        from app.config import settings
        slugs = args.models or (
            CANDIDATES["parser"] + CANDIDATES["gen"] if args.all
            else [settings.parser_model, settings.gen_model]
        )
        check(slugs)
    else:
        compare(args.models or CANDIDATES[args.tier], args.kind)


if __name__ == "__main__":
    main()
