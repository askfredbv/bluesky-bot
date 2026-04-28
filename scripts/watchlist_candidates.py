"""Candidate handles for the proactive-reply watchlist (Phase 4a recon).

Edit freely — `audit_watchlist.py` reads these lists, fetches each handle's
recent posts, and scores them. Hard exclusions per `docs/PLAN_engagement.md
§4a` are documented inline so they survive review.

Hard exclusions (do not add):
- Frontier-lab CEOs (ambulance-chasing optics)
- AI-news aggregators (amplifies noise)
- Political accounts of any stripe
- Hot-take / dunk-tank accounts
"""
from __future__ import annotations

# Bluesky handles — bare handle form, no leading @
BLUESKY_CANDIDATES: list[str] = [
    "simonwillison.net",
    "jeremyhoward.bsky.social",
    "swyx.io",
    "jbhuang.bsky.social",
    "pytorch.bsky.social",
    "huggingface.bsky.social",
    # Add Dutch/Belgian tech journalists via manual discovery; commit when found.
]

# Mastodon handles — `@user@instance` form, no leading `@` on the leftmost.
MASTODON_CANDIDATES: list[str] = [
    "simon@simonwillison.net",
    "glyph@mastodon.social",
    "inthehands@hachyderm.io",
    "b0rk@jvns.ca",
    "jwildeboer@social.wildeboer.net",
    # Add Belgian mastodon.be tech folks via manual discovery; commit when found.
]
