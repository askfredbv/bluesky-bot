"""Phase 4b — proactive reply pipeline (scaffolding).

v4.21 (2026-05-15) — first commit lands the state schema and I/O helpers
only. The scan / generate / approve flow lands in subsequent commits.
Nothing in the daily run imports this module yet.

State file (Gist-stored as ``pending_replies.json``):

    {
      "pending":  [draft, ...],
      "posted":   [draft, ...],   # kept 14 days for traction analysis
      "rejected": [draft, ...]    # kept 14 days to spot prompt drift
    }

Each ``draft`` has the shape:

    {
      "id":               "<uuid4>",
      "platform":         "bluesky",
      "parent_post_uri":  "at://did:plc:.../app.bsky.feed.post/...",
      "parent_author":    "simonwillison.net",
      "parent_text":      "...",
      "draft_reply":      "...",
      "generated_at":     "<ISO-8601 UTC>",
      "expires_at":       "<ISO-8601 UTC>"   # generated_at + DRAFT_EXPIRY_HOURS
    }

``posted`` entries gain ``posted_at`` and ``posted_uri``; ``rejected``
entries gain ``rejected_at`` and ``rejected_reason``.

Storage strategy mirrors ``src/bluesky_session.py``: Gist-only, no local
fallback. If Gist I/O fails, the worst case is one missed reply
opportunity — not days of duplicate posts (the seen-articles case that
needs the three-tier fallback).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from src.logger import SafeLogger
from src.utils import _load_gist_state, _save_gist_state

_PENDING_REPLIES_FILENAME = "pending_replies.json"


def _empty_state() -> Dict[str, List[Dict[str, Any]]]:
    """Return the canonical empty-state skeleton.

    Three buckets, all lists. Always returns a fresh dict so callers can
    mutate freely.
    """
    return {"pending": [], "posted": [], "rejected": []}


def load_pending_replies() -> Dict[str, List[Dict[str, Any]]]:
    """Load proactive-reply state from the Gist; return empty skeleton if absent.

    Defensive against partial / malformed state: any missing bucket key is
    filled with an empty list. This keeps callers free of ``.get(..., [])``
    boilerplate at every read site.
    """
    try:
        data = _load_gist_state(_PENDING_REPLIES_FILENAME)
    except Exception as e:
        SafeLogger.warn(
            "pending_replies_load_failed",
            "Could not load pending_replies from Gist; using empty state",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        return _empty_state()

    if not isinstance(data, dict):
        return _empty_state()

    state = _empty_state()
    for bucket in ("pending", "posted", "rejected"):
        value = data.get(bucket)
        if isinstance(value, list):
            state[bucket] = value
    return state


def save_pending_replies(state: Dict[str, List[Dict[str, Any]]]) -> bool:
    """Persist proactive-reply state to the Gist.

    Returns True on success, False on failure. Failures are logged at
    ERROR level (same promotion as ``gist_state_save_failed`` per the
    2026-04-22 retro callback) — losing this state silently is the same
    class of bug the duplicate-source-posts retro called out.
    """
    try:
        ok = _save_gist_state(_PENDING_REPLIES_FILENAME, state)
    except Exception as e:
        SafeLogger.error(
            "pending_replies_save_failed",
            "Failed to persist pending_replies to Gist",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        return False
    if not ok:
        SafeLogger.error(
            "pending_replies_save_failed",
            "Gist save returned False for pending_replies",
        )
    return ok


def new_draft_id() -> str:
    """Generate a fresh UUID4 string for a new draft."""
    return str(uuid.uuid4())
