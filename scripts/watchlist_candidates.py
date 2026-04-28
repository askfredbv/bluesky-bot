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
    "xeiaso.net",
    # Pruned in 2026-04-28 audit (see commit log + docs/WATCHLIST_AUDIT.md):
    # - swyx.io → moved to xeiaso.net (announced in his last bsky post)
    # - pytorch.bsky.social → squatted/repurposed account, posting US politics
    # - jbhuang.bsky.social → dormant since 2024-11
    # - jeremyhoward.bsky.social → empty / inactive on bsky
    # - huggingface.bsky.social → empty broadcast account
    #
    # Slate is intentionally short until manual discovery lands more
    # Simon-tier substantive posters. Candidates to consider (verify
    # voice/cadence before adding): Dutch/Belgian tech journalists,
    # other independent practitioner accounts that post observations
    # (not announcements). Anyone who fails the audit comes back out.
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
