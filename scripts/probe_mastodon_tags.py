"""Diagnostic: see what discovery tags the tagger actually picks.

Read-only — posts nothing, changes nothing. Runs `generate_mastodon_tags`
against the bot's own recent live posts and prints the tags beside each one,
so the tag quality can be judged on real text before it ships to the feed.

Why this exists: the tagger's whole value is whether it names a tag someone
browsing that tag would want to find. That is a judgement call about model
output, and unit tests mock the model, so they cannot answer it. The
GEMINI_API_KEY is IP-restricted to the GitHub runners, so this is the only
place the probe is truthful — same rationale as model-discovery.yml and
image-probe.yml. Trigger from the Actions UI.

Reading the output: `raw` is what the model returned, `tags` is what survives
_sanitize_mastodon_tags. A row where raw is non-empty but tags is empty means
the sanitizer rejected everything — usually the model reaching for a banned
broad tag (#AI, #tech). A few of those are the rule working; all of them means
the prompt is not steering hard enough toward specifics.
"""
import asyncio
import json
import os
import sys
import urllib.request
from typing import Any, Dict, List

from src.agents import generate_mastodon_tags, _sync_generate
from src.config import GEMINI_MODEL_PRIORITY, MASTODON_TAGS_BANNED

# The bot's own feed, via the public read-only AppView (no auth needed).
_FEED_URL = (
    "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    "?actor=askfred.be&limit={limit}&filter=posts_no_replies"
)

# Fallback corpus if the feed cannot be reached, so the probe still says
# something useful about the prompt. Shapes mirror the three modes.
_FALLBACK_POSTS = [
    "NVIDIA's next chip is a packaging story, not a silicon one. The real "
    "constraint is CoWoS capacity, not transistor density.",
    "The half-life of internal documentation is brutal. I have seen more wikis "
    "become graveyards than reference works.",
    "On this day in 1969, the first ARPANET message crashed after two letters. "
    "They got 'LO' out of 'LOGIN'.",
    "A court has ruled that published model weights are not a trade secret. "
    "That is a bigger deal for open-weight releases than the headline suggests.",
]


def _fetch_recent_posts(limit: int) -> List[str]:
    """Pull the bot's recent post text from the public Bluesky AppView."""
    req = urllib.request.Request(
        _FEED_URL.format(limit=limit),
        headers={"User-Agent": "askfred-bot-tag-probe/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data: Dict[str, Any] = json.loads(resp.read().decode())
    posts: List[str] = []
    for item in data.get("feed", []):
        record = (item.get("post") or {}).get("record") or {}
        text = (record.get("text") or "").strip()
        # Skip thread continuations; the tagger only ever sees a root post.
        if text and (record.get("reply") is None):
            posts.append(text)
    return posts


def _raw_tags(post: str) -> Any:
    """One un-sanitized call, so the printout separates 'the model said' from
    'the sanitizer allowed'."""
    from src.agents import _MASTODON_TAG_PROMPT

    text = _sync_generate(
        os.environ["GEMINI_API_KEY"],
        "You return only JSON. No prose, no explanation.",
        _MASTODON_TAG_PROMPT.format(post=post),
        GEMINI_MODEL_PRIORITY[0],
    )
    return json.loads(text.replace("```json", "").replace("```", "").strip())


async def _run(posts: List[str]) -> int:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        print("GEMINI_API_KEY not set", file=sys.stderr)
        return 1

    print(f"model:  {GEMINI_MODEL_PRIORITY[0]}")
    print(f"banned: {', '.join(MASTODON_TAGS_BANNED)}\n")

    empty = 0
    for idx, post in enumerate(posts, 1):
        preview = post.replace("\n", " ")
        if len(preview) > 150:
            preview = preview[:149] + "…"
        print(f"[{idx}] {preview}")
        # Two calls per post: the raw one shows what the model reached for,
        # the second exercises the real function the bot uses. Both are cheap
        # and the raw/sanitized gap is the whole diagnostic.
        try:
            print(f"    raw : {_raw_tags(post)}")
        except Exception as exc:
            print(f"    raw : FAILED {type(exc).__name__}: {str(exc)[:120]}")
        tags = await generate_mastodon_tags(key, [post])
        print(f"    tags: {' '.join(tags) if tags else '(none)'}\n")
        if not tags:
            empty += 1

    total = len(posts)
    print(f"summary: {total - empty}/{total} posts got tags, {empty} untagged")
    if empty == total:
        print("  ALL posts came back untagged — check the prompt or the ban list "
              "before shipping.")
    return 0


def main() -> int:
    limit = int(os.environ.get("PROBE_POST_LIMIT", "10"))
    try:
        posts = _fetch_recent_posts(limit)
        source = "live Bluesky feed"
    except Exception as exc:
        print(f"(feed fetch failed: {type(exc).__name__}: {str(exc)[:120]} "
              f"— falling back to sample posts)")
        posts, source = list(_FALLBACK_POSTS), "built-in samples"
    if not posts:
        posts, source = list(_FALLBACK_POSTS), "built-in samples"
    print(f"source: {source} ({len(posts)} posts)\n")
    return asyncio.run(_run(posts))


if __name__ == "__main__":
    raise SystemExit(main())
