"""Diagnostic: see what discovery tags the tagger actually picks.

Read-only — posts nothing, changes nothing. Runs the real tagging call against
the bot's own recent live posts and prints the tags beside each one, so tag
quality can be judged on real text before it ships to the feed.

Why this exists: the tagger's whole value is whether it names a tag someone
browsing that tag would want to find. That is a judgement call about model
output, and unit tests mock the model, so they cannot answer it. The
GEMINI_API_KEY is IP-restricted to the GitHub runners, so this is the only
place the probe is truthful — same rationale as model-discovery.yml and
image-probe.yml. Trigger from the Actions UI.

Reading the output: `raw` is what the model returned and `tags` is what
survives the sanitizer, both from the SAME call — so the gap between them is
attributable to sanitizing and nothing else. `post ends` shows the suffix a
Mastodon reader would actually see, after the broadcaster's ceiling and length
handling. A few rows where tags is empty is the exclusion rule working; all of
them means the prompt is not steering toward specifics.

Calls request_mastodon_tags directly, which carries no enablement gate, so this
stays truthful while MASTODON_TAGS_ENABLED is False in production.
"""
import asyncio
import json
import os
import re
import sys
import urllib.request
from typing import Any, Dict, List

from src.agents import request_mastodon_tags
from src.broadcasters import apply_mastodon_tags
from src.config import (
    GEMINI_MODEL_PRIORITY, MASTODON_TAGS_EXCLUDED, MAX_HASHTAGS_PER_POST,
)

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

_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")


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


async def _probe_one(key: str, model: str, idx: int, post: str) -> bool:
    """Print one post's tagging result. Returns True if it ended up tagged."""
    preview = post.replace("\n", " ")
    if len(preview) > 150:
        preview = preview[:149] + "…"
    print(f"[{idx}] {preview}")

    spent = len(_HASHTAG_RE.findall(post))
    allowance = MAX_HASHTAGS_PER_POST - spent
    if allowance <= 0:
        print(f"    tags: (none — the post already spends the "
              f"{MAX_HASHTAGS_PER_POST}-hashtag ceiling)\n")
        return False

    # ONE call: raw and tags come from the same response, so the gap between
    # them is attributable to the sanitizer and nothing else.
    try:
        raw, tags = await request_mastodon_tags(key, post, model, allowance)
    except Exception as exc:
        print(f"    FAILED {type(exc).__name__}: {str(exc)[:140]}\n")
        return False

    print(f"    raw : {raw}")
    print(f"    tags: {' '.join(tags) if tags else '(none)'}")
    # What a Mastodon reader would actually see, after the broadcaster's
    # dedup / ceiling / length handling.
    suffix = apply_mastodon_tags([post], tags)[0][len(post):]
    print(f"    ends: {suffix!r}\n" if suffix else "    ends: (unchanged)\n")
    return bool(tags)


async def _run(posts: List[str]) -> int:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        print("GEMINI_API_KEY not set", file=sys.stderr)
        return 1

    model = GEMINI_MODEL_PRIORITY[0]
    print(f"model:    {model}")
    print(f"excluded: {', '.join(MASTODON_TAGS_EXCLUDED)}")
    print(f"ceiling:  {MAX_HASHTAGS_PER_POST} per post, shared with the "
          f"generated text\n")

    tagged = 0
    for idx, post in enumerate(posts, 1):
        if await _probe_one(key, model, idx, post):
            tagged += 1

    total = len(posts)
    print(f"summary: {tagged}/{total} posts got tags, {total - tagged} untagged")
    if tagged == 0:
        print("  NOTHING got tagged — check the prompt or the exclusion list "
              "before flipping MASTODON_TAGS_ENABLED.")
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
