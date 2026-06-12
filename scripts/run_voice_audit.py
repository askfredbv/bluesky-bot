"""Monthly voice-audit entry point — an independent critic, not an auto-fixer.

Pulls the bot's recent posts and asks a Pro-tier Gemini model (a DIFFERENT,
more-capable model than the gemini-3.5-flash writer, for genuine independence)
to critique them against Frederik's voice. Prints a markdown report that the
voice-audit workflow turns into a GitHub issue for human review.

Deliberately NOT plat bandwerk: this surfaces a draft critique for judgement.
Nothing is auto-applied. The report leads with a "verify before acting" note,
and the findings are claims to check against the live feed — exactly the
two-way discipline used for Codex code reviews. See docs/VOICE_AUDIT.md for
the canonical human-facing version of the prompt + how to judge the output.

    python -m scripts.run_voice_audit

Reads GEMINI_API_KEY from the environment (or .env). Best-effort: always
exits 0 — a flaky audit run should not page anyone.
"""

from __future__ import annotations

import os

import httpx

# Auditor models, newest-capable first. ALL are Pro-tier and DIFFERENT from
# the gemini-3.5-flash writer — the independence is the point. Tried in order;
# the first that responds is used. 3.5-pro may not be GA to every key yet
# (it was rolling out 2026-06), so 3.x Pro previews are the fallbacks.
_AUDITOR_MODELS = [
    "gemini-3.5-pro",
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
]

_POSTS_TO_AUDIT = 12

# Mirror of the prompt in docs/VOICE_AUDIT.md (canonical human-facing copy).
# Keep the two in sync if the voice canon changes.
_VOICE_AUDIT_PROMPT = """\
You are an independent voice critic for the tech account @askfred.be. The
voice is Frederik Van Hecke's. Judge whether each post below holds the voice
or drifts to generic AI prose. Be specific: quote exact phrases. No general
praise. Report findings plainly — no hype or motivational phrasing in your
own analysis (that is the very thing you are critiquing).

THE VOICE (two registers):
- Register A (strategic-advisory): longer sentences, NO contractions ("it is",
  not "it's"), careful argument, a sharp closing sentence. E.g. "The tool
  rarely fails. Adoption fails."
- Register B (casual-narrative): short sentences, contractions OK, dry
  understatement, concrete brands/prices/numbers. E.g. "In short: that does
  not fly."

FINGERPRINT (both): dry understatement, em-dashes for clarification, first-
person presence, concrete specifics (numbers, names, mechanisms), NO hype, NO
motivational-poster, leads with the finding.

WHAT DRIFT LOOKS LIKE (flag it):
- generic-clever phrasing with no concrete anchor
- borrowed clichés ("fails to survive contact with reality", "a different
  game entirely", "the bill is coming due")
- summary-form instead of a stance of one's own
- jargon density that shuts the reader out
- absence of first person where it would be natural
- TEMPLATE OPENINGS repeated across posts ("The most interesting bit in this
  paper…", "The bit that landed for me…") — fine once, an AI tell when every
  external-content post opens the same way

TASK:
1. Per post: one line — does it hold the voice, or where does it slip? Quote it.
2. The 2-3 weakest posts, with exactly why.
3. One thing the account does consistently well, and one recurring weakness.
If nothing is substantively wrong, say so plainly rather than inventing nits.

THE POSTS:
"""


def _fetch_recent_posts(limit: int = _POSTS_TO_AUDIT) -> list[str]:
    """Pull the bot's recent original posts via the public Bluesky API."""
    resp = httpx.get(
        "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
        params={"actor": "askfred.be", "limit": limit, "filter": "posts_no_replies"},
        timeout=15,
    )
    resp.raise_for_status()
    posts = []
    for item in resp.json().get("feed", []):
        text = item.get("post", {}).get("record", {}).get("text", "")
        if text.strip():
            posts.append(text.strip())
    return posts


def _build_task(posts: list[str]) -> str:
    numbered = "\n\n".join(f"{i}. {p}" for i, p in enumerate(posts, 1))
    return _VOICE_AUDIT_PROMPT + numbered


def _run_auditor(api_key: str, task: str) -> tuple[str, str] | tuple[None, None]:
    """Call the first available Pro-tier auditor model. Returns (model, text)."""
    from google import genai

    client = genai.Client(api_key=api_key)
    for model in _AUDITOR_MODELS:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=task,
                config={"max_output_tokens": 4096},
            )
            text = (resp.text or "").strip()
            if text:
                return model, text
        except Exception:
            continue
    return None, None


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:  # pragma: no cover
        pass

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        print("## Voice audit — could not run\n\nGEMINI_API_KEY not set.")
        return 0

    try:
        posts = _fetch_recent_posts()
    except Exception as e:
        print(f"## Voice audit — could not run\n\nFeed fetch failed: {type(e).__name__}: {e}")
        return 0
    if not posts:
        print("## Voice audit — nothing to audit\n\nNo recent posts found.")
        return 0

    model, critique = _run_auditor(api_key, _build_task(posts))
    if not critique:
        print("## Voice audit — could not run\n\nNo auditor model responded.")
        return 0

    # The report leads with the human-in-the-loop framing — this is a draft
    # critic to weigh, NOT a verdict to apply. Keeps it from becoming rote.
    print(
        f"## Voice audit ({model})\n\n"
        f"_Independent draft critique of the last {len(posts)} posts. "
        "These are **claims to verify** against the live feed before acting, "
        "not verdicts — same two-way discipline as the Codex code reviews. "
        "Voice is a brand decision; act only on what holds up._\n\n"
        "---\n\n"
        f"{critique}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
