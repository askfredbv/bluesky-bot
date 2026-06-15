"""Template for the proactive-reply watchlist candidates (Phase 4a recon).

Copy this to `scripts/watchlist_candidates.py` and fill in real handles.
The real file is GITIGNORED — the repo is public, and a committed list of
named people the bot "scores" for reply-worthiness is needlessly exposing.
Keep the actual candidates local; `audit_watchlist.py` reads them and writes
its scored report to `docs/WATCHLIST_AUDIT.md` (also gitignored).

`audit_watchlist.py` falls back to these placeholder lists if the real file
is absent, so a fresh clone still imports cleanly — it just audits nobody
until you create your own copy.

Hard exclusions (do not add): frontier-lab CEOs, AI-news aggregators,
political accounts, hot-take / dunk-tank accounts.
"""
from __future__ import annotations

# Bluesky handles — bare handle form, no leading @
BLUESKY_CANDIDATES: list[str] = [
    # "example-practitioner.bsky.social",
]

# Mastodon handles — `@user@instance` form, no leading `@` on the leftmost.
MASTODON_CANDIDATES: list[str] = [
    # "user@instance.example",
]
