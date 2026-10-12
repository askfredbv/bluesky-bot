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
    ANTHROPIC_MODELS,
    ANTHROPIC_CHAIN_POSITION,
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


async def one_run(
    anthropic_key: str, gemini_key: str, index: int, outage: str
) -> bool:
    """Fail one provider's whole rung, then report what the other produced.

    `outage` names the provider that is simulated DOWN. The useful direction
    is whichever rung production does not exercise: with Anthropic primary the
    daily run never reaches Gemini, so "anthropic" is the default. Flip it to
    "gemini" to re-prove the Anthropic rung in isolation.
    """
    import src.agents as agents

    real = agents._sync_generate
    tried: list[str] = []
    gemini_is_down = outage == "gemini"

    def _one_provider_is_down(api_key, instr, task, model, anthropic_api_key=None):
        tried.append(model)
        if is_anthropic_model(model) != gemini_is_down:
            raise RuntimeError(f"simulated {outage} outage")
        return real(api_key, instr, task, model, anthropic_api_key)

    # Always walk the full chain in production order, so the probe reports the
    # real sequence rather than a convenient one.
    chain = (
        ANTHROPIC_MODELS + GEMINI_MODEL_PRIORITY
        if ANTHROPIC_CHAIN_POSITION == "primary"
        else GEMINI_MODEL_PRIORITY + ANTHROPIC_MODELS
    )

    agents._sync_generate = _one_provider_is_down
    try:
        posts, topic, link = await generate_content(
            gemini_key,
            [],
            mode=Mode.CURATOR,
            news_items=ITEMS,
            model_priority=chain,
            anthropic_api_key=anthropic_key,
        )
    finally:
        agents._sync_generate = real

    survivor_reached = [m for m in tried if is_anthropic_model(m) == gemini_is_down]
    print(f"\n--- run {index} ---")
    print(f"  chain walked : {' -> '.join(tried)}")
    if not survivor_reached:
        print(f"  FAIL: the chain never reached a non-{outage} model")
        return False
    if not posts:
        print("  FAIL: the surviving rung produced nothing publishable "
              "(validators rejected it)")
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
    # Default to failing whichever provider currently writes the posts, because
    # the rung worth probing is the one production never reaches.
    ap.add_argument(
        "--outage",
        choices=("anthropic", "gemini"),
        default=os.environ.get("PROBE_OUTAGE")
        or ("anthropic" if ANTHROPIC_CHAIN_POSITION == "primary" else "gemini"),
        help="which provider to simulate as down",
    )
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
        print("ANTHROPIC_API_KEY is not set. The Anthropic rung is unarmed.")
        return 1

    # Needed for real, not as a placeholder: with --outage anthropic the Gemini
    # rung has to actually answer, so a dummy key fails all four models and the
    # probe reports a false negative.
    gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not gemini_key:
        print("GEMINI_API_KEY is not set; the Gemini rung cannot be exercised.")
        return 1

    down = GEMINI_MODEL_PRIORITY if args.outage == "gemini" else ANTHROPIC_MODELS
    survives = ANTHROPIC_MODELS if args.outage == "gemini" else GEMINI_MODEL_PRIORITY
    print(f"Anthropic rung: {ANTHROPIC_MODELS}  (position: {ANTHROPIC_CHAIN_POSITION})")
    print(f"Simulating a total {args.outage} outage across {down}")
    print(f"Expecting the run to be carried by {survives}")

    results = [
        await one_run(key, gemini_key, i, args.outage)
        for i in range(1, args.runs + 1)
    ]
    ok = sum(results)
    print(f"\n{ok}/{len(results)} runs produced a publishable post.")
    if ok != len(results):
        print(f"The non-{args.outage} rung is NOT reliable in its current state.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
