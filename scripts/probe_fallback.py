"""Diagnostic: prove the cross-provider fallback still works.

Read-only — posts nothing, changes nothing.

Why this exists
---------------
The Anthropic model sits at the BOTTOM of the chain, below four Gemini models.
In normal operation it never runs, which means no amount of watching daily runs
tells you whether it still works. An insurance path nobody has exercised is the
backup that turns out not to restore. This is the only way to find out on a
good day instead of a bad one.

Unit tests mock the SDK, so they prove the dispatch is wired correctly and
nothing more. They cannot catch an expired key, a retired model id, a changed
request shape, or output that stops satisfying the Curator's JSON contract.
Those are exactly the failures that matter here.

What it does
------------
1. Simulates the real outage: every Gemini model raises, so ``generate_content``
   walks the chain exactly as it would in production and lands on Anthropic.
2. Runs the genuine Curator prompt over real headlines, then applies the same
   gates the live path applies — JSON parse, ``_validate_thread_shape``,
   ``validate_summary``, ``_apply_voice_trim`` — and prints what came back.
3. Repeats ``--runs`` times, because one sample proves little against a
   stochastic generator.

Exit code is non-zero if any run fails to produce a publishable post, so this
can be read at a glance or wired into a scheduled check later.

Trigger from the Actions UI, or locally with ANTHROPIC_API_KEY set.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents import generate_content, is_anthropic_model  # noqa: E402
from src.config import (  # noqa: E402
    ANTHROPIC_FALLBACK_MODELS,
    GEMINI_MODEL_PRIORITY,
    MAX_POST_LENGTH_BSKY,
    Mode,
)

# Fixed headlines rather than a live feed fetch: the probe is testing the
# fallback, not the news pipeline, and a stable input makes two runs on
# different days comparable.
ITEMS = [
    {
        "title": "Python 3.15 released",
        "description": (
            "Adds sentinel and frozendict built-in types, switches to UTF-8 as the "
            "default encoding, and improves the experimental JIT compiler."
        ),
        "link": "https://lwn.net/Articles/1099602/",
    },
    {
        "title": "Deno is joining Cloudflare",
        "description": "The Deno runtime and its team move under Cloudflare.",
        "link": "https://simonwillison.net/2026/Oct/9/deno-is-joining-cloudflare/",
    },
]


async def one_run(anthropic_key: str, index: int) -> bool:
    """Force every Gemini model to fail, then report what Anthropic produced."""
    import src.agents as agents

    real = agents._sync_generate
    tried: list[str] = []

    def _gemini_is_down(api_key, instr, task, model, anthropic_api_key=None):
        tried.append(model)
        if not is_anthropic_model(model):
            raise RuntimeError("simulated Gemini outage")
        return real(api_key, instr, task, model, anthropic_api_key)

    agents._sync_generate = _gemini_is_down
    try:
        posts, topic, link = await generate_content(
            "gemini-key-intentionally-unused",
            [],
            mode=Mode.CURATOR,
            news_items=ITEMS,
            model_priority=GEMINI_MODEL_PRIORITY + ANTHROPIC_FALLBACK_MODELS,
            anthropic_api_key=anthropic_key,
        )
    finally:
        agents._sync_generate = real

    reached = [m for m in tried if is_anthropic_model(m)]
    print(f"\n--- run {index} ---")
    print(f"  chain walked : {' -> '.join(tried)}")
    if not reached:
        print("  FAIL: the chain never reached an Anthropic model")
        return False
    if not posts:
        print("  FAIL: fallback produced nothing publishable (validators rejected it)")
        return False

    print(f"  chose        : {link}")
    print(f"  topic        : {topic}")
    for i, post in enumerate(posts, 1):
        over = "  <-- OVER LIMIT" if len(post) > MAX_POST_LENGTH_BSKY else ""
        print(f"  post {i} ({len(post)} chars){over}")
        print(f"    {post}")
    return True


async def main() -> int:
    ap = argparse.ArgumentParser()
    # `or "3"` rather than a dict default: a workflow_dispatch input that is
    # present but empty arrives as "", which int() would reject.
    ap.add_argument("--runs", type=int, default=int(os.environ.get("PROBE_RUNS") or "3"))
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Same convenience as scripts/discover_models.py: in CI the key comes
        # from secrets, locally from a .env beside the checkout.
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        print("ANTHROPIC_API_KEY is not set. The fallback is unarmed in production.")
        return 1

    print(f"Fallback models: {ANTHROPIC_FALLBACK_MODELS}")
    print(f"Simulating a total Gemini outage across {GEMINI_MODEL_PRIORITY}")

    results = [await one_run(key, i) for i in range(1, args.runs + 1)]
    ok = sum(results)
    print(f"\n{ok}/{len(results)} runs produced a publishable post.")
    if ok != len(results):
        print("The fallback is NOT reliable in its current state.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
