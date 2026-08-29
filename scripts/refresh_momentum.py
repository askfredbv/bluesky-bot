"""Refresh MOMENTUM_PRODUCTS from the live news cycle.

`MOMENTUM_PRODUCTS` in `config.py` is the list of flagship AI product / model
names that earn a scoring bonus (gpt-5, claude 4, …). It was maintained by hand
"quarterly", so it drifts: last year's flagships stop being news and this
year's aren't boosted. This script reads recent RSS headlines, asks Gemini for
the currently-trending flagship names, sanitises them hard, and rewrites the
list in `config.py`.

Idea borrowed from strike007-3000/BluBot's weekly config updater — adapted to
this repo's discipline: the workflow opens a **PR** (never pushes to main), so
every change goes through Codex + CI + a human merge, and the AI output is
strictly sanitised before it is ever written into source.

Run: `python -m scripts.refresh_momentum` (needs GEMINI_API_KEY; the key is
IP-restricted, so it only reaches the API from the GitHub runners). Best-effort:
prints a summary and exits 0; a flaky run must not page anyone.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import feedparser
import httpx

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.agents import _thinking_budget_for  # noqa: E402  (reuse canonical rule)
from src.config import GEMINI_MODEL_PRIORITY, RSS_FEEDS  # noqa: E402
from src.logger import SafeLogger  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "src" / "config.py"

MAX_HEADLINES = 150
MAX_PRODUCTS = 18          # hard cap on how many names we will write
MIN_PRODUCTS = 8           # below this we do not trust the model — skip, don't shrink
MAX_NAME_LEN = 30          # a flagship name is short; longer = model rambling
# A momentum name is a product/model string: letters, digits, spaces, and the
# few punctuation marks that show up in real names (gpt-4.5, o3, deepseek-v4).
# Anything else is rejected — this is the injection guard: nothing that isn't
# matched here can reach the quoted list literal in config.py.
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9 .\-]{0,%d}$" % (MAX_NAME_LEN - 1))

_PROMPT = (
    "From the AI/tech news headlines below, list the flagship AI products and "
    "models that are current momentum — the named systems a post about which is "
    "categorically more consequential than a generic 'new feature' story "
    "(e.g. gpt-5, claude 4, llama 4, gemini 3, grok 4, deepseek v4). Include "
    "both shipped and clearly-anticipated named systems. Exclude generic terms, "
    "companies, people, and events.\n\n"
    "Return ONLY a JSON array of 12-18 lowercase strings, no prose. Each string "
    "is a short product/model name (letters, digits, spaces, dots, hyphens only)."
    "\n\nHEADLINES:\n{headlines}"
)


async def _fetch_headlines() -> list[str]:
    """Pull the first few headlines from each configured feed, in parallel."""
    async def one(client: httpx.AsyncClient, url: str) -> list[str]:
        try:
            resp = await client.get(url, timeout=10)
            feed = feedparser.parse(resp.content)
            return [t for e in feed.entries[:5] if (t := getattr(e, "title", "").strip())]
        except Exception as exc:  # best-effort per feed
            SafeLogger.warn("momentum_feed_fetch_failed", "Feed fetch failed",
                            url=url, error_type=type(exc).__name__, error_msg=str(exc)[:200])
            return []

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        results = await asyncio.gather(*(one(client, u) for u in RSS_FEEDS))
    headlines: list[str] = [h for sub in results for h in sub]
    return headlines[:MAX_HEADLINES]


def _ask_gemini(api_key: str, headlines: list[str]) -> list[str]:
    """Ask the model chain for trending flagship names; return sanitised names.

    Failover only ends when a model yields at least MIN_PRODUCTS *usable* names.
    A response that parses to a nonempty list but sanitises to too few (garbage,
    over-long, or injection-shaped entries) is not a success — keep trying the
    next model. Returns [] if the whole chain fails.
    """
    from google import genai

    client = genai.Client(api_key=api_key)
    task = _PROMPT.format(headlines="\n".join(headlines))
    for model in GEMINI_MODEL_PRIORITY:
        try:
            # Pin thinking budget (canonical rule from agents.py): the 3.x/2.5
            # chain runs thinking by default, which would eat the 512-token cap
            # and can return empty output. None => send no thinking_config.
            config: dict = {"max_output_tokens": 512}
            budget = _thinking_budget_for(model)
            if budget is not None:
                config["thinking_config"] = {"thinking_budget": budget}
            resp = client.models.generate_content(
                model=model, contents=task, config=config)
            text = (resp.text or "").strip()
            cleaned = sanitize_products(_parse_json_array(text) or [])
            if len(cleaned) >= MIN_PRODUCTS:
                SafeLogger.info("momentum_model_used", "Model returned usable names",
                                model=model, count=len(cleaned))
                return cleaned
            SafeLogger.warn("momentum_model_thin",
                            "Too few usable names, trying next model",
                            model=model, count=len(cleaned))
        except Exception as exc:
            SafeLogger.warn("momentum_model_failed", "Model call failed",
                            model=model, error_type=type(exc).__name__, error_msg=str(exc)[:200])
    return []


def _parse_json_array(text: str) -> list[str] | None:
    """Extract a JSON array of strings from the model output (tolerant of fences)."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def sanitize_products(raw: list) -> list[str]:
    """Hard allowlist filter: only short, safe product-name strings survive.

    This is the injection guard — the output is written verbatim (quoted) into
    config.py, so anything not matching _SAFE_NAME is dropped. Deduped, order
    preserved, capped at MAX_PRODUCTS.
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip().lower()
        if name and name not in seen and _SAFE_NAME.match(name):
            seen.add(name)
            out.append(name)
        if len(out) >= MAX_PRODUCTS:
            break
    return out


def _format_list_body(items: list[str]) -> str:
    """Render items as config.py list rows: 5 per line, 4-space indent, quoted."""
    lines = []
    for i in range(0, len(items), 5):
        chunk = ", ".join(f'"{x}"' for x in items[i:i + 5])
        lines.append(f"    {chunk},")
    return "\n".join(lines)


def rewrite_momentum_products(source: str, items: list[str]) -> str:
    """Replace the MOMENTUM_PRODUCTS list body in a config.py source string.

    Pure/text-only so it is unit-testable without touching disk. Raises if the
    anchor is missing (fail loud rather than silently no-op).
    """
    pattern = re.compile(
        r"(MOMENTUM_PRODUCTS: List\[str\] = \[\n)(.*?)(\n\])", re.DOTALL)
    if not pattern.search(source):
        raise ValueError("MOMENTUM_PRODUCTS list literal not found in config source")
    return pattern.sub(lambda m: m.group(1) + _format_list_body(items) + m.group(3),
                       source, count=1)


async def _run() -> int:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        print("refresh_momentum: GEMINI_API_KEY not set — skipping.")
        return 0

    headlines = await _fetch_headlines()
    if len(headlines) < 10:
        print(f"refresh_momentum: only {len(headlines)} headlines — too few, skipping.")
        return 0

    products = _ask_gemini(api_key, headlines)
    if len(products) < MIN_PRODUCTS:
        print(f"refresh_momentum: model returned {len(products)} usable names — "
              "too few to trust, skipping to avoid shrinking the list.")
        return 0

    source = CONFIG_PATH.read_text(encoding="utf-8")
    updated = rewrite_momentum_products(source, products)
    if updated == source:
        print("refresh_momentum: MOMENTUM_PRODUCTS already current — no change.")
        return 0

    CONFIG_PATH.write_text(updated, encoding="utf-8", newline="\n")
    print(f"refresh_momentum: updated MOMENTUM_PRODUCTS to {len(products)} names:")
    print("  " + ", ".join(products))
    return 0


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:  # pragma: no cover
        pass
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
