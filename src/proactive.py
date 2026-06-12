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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.config import (
    BANNED_QUESTION_PATTERNS,
    PROACTIVE_REPLY_DRAFT_EXPIRY_HOURS,
    PROACTIVE_REPLY_MAX_PARENT_AGE_HOURS,
    PROACTIVE_REPLY_MIN_PARENT_ENGAGEMENT,
    PROACTIVE_REPLY_PER_HANDLE_COOLDOWN_DAYS,
)
from src.logger import SafeLogger
from src.utils import _load_gist_state_strict, _save_gist_state

_PENDING_REPLIES_FILENAME = "pending_replies.json"


def _empty_state() -> Dict[str, List[Dict[str, Any]]]:
    """Return the canonical empty-state skeleton.

    Three buckets, all lists. Always returns a fresh dict so callers can
    mutate freely.
    """
    return {"pending": [], "posted": [], "rejected": []}


def load_pending_replies() -> Tuple[Dict[str, List[Dict[str, Any]]], bool]:
    """Load proactive-reply state from the Gist.

    Returns ``(state, trusted)``:
      - ``state`` is the canonical 3-bucket skeleton, with any missing/
        malformed bucket filled as an empty list (callers stay free of
        ``.get(..., [])`` boilerplate).
      - ``trusted`` is ``False`` when the Gist read itself failed — meaning
        the empty ``state`` is "could not read", NOT "no drafts exist".

    Callers MUST honour ``trusted``: persisting an empty state after an
    untrusted read overwrites real pending/posted/rejected history. The
    scan should skip the run; the approval workflow should exit without
    saving. (Codex review 2026-06-12; AGENTS.md "fail loud on critical
    state".)
    """
    try:
        data, trusted = _load_gist_state_strict(_PENDING_REPLIES_FILENAME)
    except Exception as e:
        SafeLogger.error(
            "pending_replies_load_failed",
            "Could not load pending_replies from Gist; treating state as untrusted",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        return _empty_state(), False

    if not trusted:
        return _empty_state(), False

    state = _empty_state()
    if isinstance(data, dict):
        for bucket in ("pending", "posted", "rejected"):
            value = data.get(bucket)
            if isinstance(value, list):
                state[bucket] = value
    return state, True


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


# ── Time helpers ────────────────────────────────────────────────────────────

def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse a Bluesky-style ISO timestamp, returning None on failure.

    Bluesky emits ``2026-05-21T18:00:00.000Z``; ``fromisoformat`` on Python
    3.11+ handles the trailing Z if we swap it for ``+00:00``. Tolerant of
    missing milliseconds and whitespace.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ── Expiry + cooldown ───────────────────────────────────────────────────────

def expire_old_drafts(
    state: Dict[str, List[Dict[str, Any]]],
    now: datetime,
) -> int:
    """Move pending drafts older than ``PROACTIVE_REPLY_DRAFT_EXPIRY_HOURS``
    to the ``rejected`` bucket with ``rejected_reason=expired``.

    Returns the number of drafts moved. Mutates ``state`` in place; the
    caller is responsible for ``save_pending_replies`` afterwards.

    Runs at the start of each scan so stale drafts don't linger forever
    when the human approval gate goes quiet.
    """
    cutoff = now - timedelta(hours=PROACTIVE_REPLY_DRAFT_EXPIRY_HOURS)
    survivors: List[Dict[str, Any]] = []
    moved = 0
    for draft in state.get("pending", []):
        generated = _parse_iso(draft.get("generated_at", ""))
        if generated is None or generated < cutoff:
            draft_copy = dict(draft)
            draft_copy["rejected_at"] = now.isoformat()
            draft_copy["rejected_reason"] = "expired"
            state.setdefault("rejected", []).append(draft_copy)
            moved += 1
        else:
            survivors.append(draft)
    state["pending"] = survivors
    if moved:
        SafeLogger.info(
            "proactive_drafts_expired",
            "Moved stale pending drafts to rejected",
            moved=moved,
            cutoff_hours=PROACTIVE_REPLY_DRAFT_EXPIRY_HOURS,
        )
    return moved


def is_handle_in_cooldown(
    handle: str,
    state: Dict[str, List[Dict[str, Any]]],
    now: datetime,
) -> bool:
    """Has this handle received an approved reply within the cooldown window?

    Checks the ``posted`` bucket — drafts that were approved and shipped.
    Pending/rejected drafts don't count (we WANT to be allowed to scan
    again after a rejection).
    """
    cutoff = now - timedelta(days=PROACTIVE_REPLY_PER_HANDLE_COOLDOWN_DAYS)
    for entry in state.get("posted", []):
        if entry.get("parent_author") != handle:
            continue
        posted_at = _parse_iso(entry.get("posted_at", ""))
        if posted_at is not None and posted_at >= cutoff:
            return True
    return False


# ── Filter ──────────────────────────────────────────────────────────────────

def _has_quote_embed(record: Any) -> bool:
    """Detect a quote-post embed without importing atproto's model types.

    Quote-posts carry an embed of type ``app.bsky.embed.record`` (or
    ``recordWithMedia``). Duck-typed: if the embed has a ``record``
    attribute pointing somewhere, treat as quote. Tolerates plain dicts
    too — useful for tests and defensive against SDK shape changes.
    """
    embed = getattr(record, "embed", None)
    if embed is None:
        return False
    if hasattr(embed, "record") and getattr(embed, "record", None) is not None:
        return True
    if isinstance(embed, dict) and embed.get("record") is not None:
        return True
    return False


def filter_feed_item(item: Any, now: datetime) -> Tuple[bool, str]:
    """Decide whether a single feed-view item is a reply candidate.

    Returns ``(passes, reason)`` where ``reason`` is a short tag suitable
    for structured logging. Designed so every rejection rule can be
    unit-tested in isolation.

    Rules (first failure wins):
      - Reposts of others (``item.reason`` set) → ``is_repost``
      - Reply posts (``post.record.reply`` set) → ``is_reply``
      - Quote-posts (``post.record.embed.record``) → ``is_quote``
      - Empty / missing text → ``no_text``
      - Older than ``PROACTIVE_REPLY_MAX_PARENT_AGE_HOURS`` → ``too_old``
      - Engagement (replies + reposts) below the floor → ``no_engagement``
      - Parent text matches ``BANNED_QUESTION_PATTERNS`` → ``reader_bait``
    """
    if getattr(item, "reason", None) is not None:
        return False, "is_repost"

    post = getattr(item, "post", None)
    if post is None:
        return False, "no_post"

    record = getattr(post, "record", None)
    if record is None:
        return False, "no_record"

    if getattr(record, "reply", None) is not None:
        return False, "is_reply"

    if _has_quote_embed(record):
        return False, "is_quote"

    text = getattr(record, "text", "") or ""
    if not text.strip():
        return False, "no_text"

    indexed_at = _parse_iso(getattr(post, "indexed_at", "") or "")
    if indexed_at is None:
        return False, "no_timestamp"
    age_hours = (now - indexed_at).total_seconds() / 3600.0
    if age_hours > PROACTIVE_REPLY_MAX_PARENT_AGE_HOURS:
        return False, "too_old"

    reposts = int(getattr(post, "repost_count", 0) or 0)
    replies = int(getattr(post, "reply_count", 0) or 0)
    if reposts + replies < PROACTIVE_REPLY_MIN_PARENT_ENGAGEMENT:
        return False, "no_engagement"

    lowered = text.lower()
    if any(pattern in lowered for pattern in BANNED_QUESTION_PATTERNS):
        return False, "reader_bait"

    return True, "ok"


def _item_to_candidate(item: Any, handle: str) -> Dict[str, Any]:
    """Project a passing feed item into a candidate dict.

    Candidate shape — internal only, lives in memory between
    ``scan_watchlist`` and ``stage_draft_reply``. The persisted draft
    schema is a superset (adds id + draft_reply + timestamps).
    """
    post = item.post
    record = post.record
    reposts = int(getattr(post, "repost_count", 0) or 0)
    replies = int(getattr(post, "reply_count", 0) or 0)
    return {
        "platform": "bluesky",
        "parent_author": handle,
        "parent_post_uri": getattr(post, "uri", ""),
        "parent_text": getattr(record, "text", "") or "",
        "engagement": reposts + replies,
        "indexed_at": getattr(post, "indexed_at", "") or "",
    }


# ── Scan ────────────────────────────────────────────────────────────────────

# Bluesky's `get_author_feed` returns ~20 by default; we ask for the same.
# All filtering happens client-side from this batch — cheaper than paging.
_FEED_FETCH_LIMIT: int = 20


async def scan_handle(
    bsky_client: Any,
    handle: str,
    state: Dict[str, List[Dict[str, Any]]],
    now: datetime,
) -> List[Dict[str, Any]]:
    """Fetch + filter posts for one handle, returning passing candidates.

    Short-circuits when the handle is in cooldown — we don't bother
    fetching the feed in that case. Logs a structured event for each
    rejection reason so we can spot filter-drift from the Actions
    output.
    """
    if is_handle_in_cooldown(handle, state, now):
        SafeLogger.info(
            "proactive_scan_handle_skipped",
            "Handle in cooldown window; skipping scan",
            handle=handle,
            cooldown_days=PROACTIVE_REPLY_PER_HANDLE_COOLDOWN_DAYS,
        )
        return []

    try:
        response = await bsky_client.app.bsky.feed.get_author_feed(
            {"actor": handle, "limit": _FEED_FETCH_LIMIT}
        )
    except Exception as e:
        SafeLogger.warn(
            "proactive_scan_handle_fetch_failed",
            "Author-feed fetch failed; skipping handle",
            handle=handle,
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        return []

    feed = getattr(response, "feed", None) or []
    candidates: List[Dict[str, Any]] = []
    rejection_counts: Dict[str, int] = {}
    for item in feed:
        passes, reason = filter_feed_item(item, now)
        if passes:
            candidates.append(_item_to_candidate(item, handle))
        else:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    SafeLogger.info(
        "proactive_scan_handle_completed",
        "Author-feed scan completed",
        handle=handle,
        fetched=len(feed),
        passed=len(candidates),
        **{f"rejected_{k}": v for k, v in rejection_counts.items()},
    )
    return candidates


async def scan_watchlist(
    bsky_client: Any,
    watchlist: List[str],
    state: Dict[str, List[Dict[str, Any]]],
    now: datetime,
) -> List[Dict[str, Any]]:
    """Orchestrate ``scan_handle`` across the configured watchlist.

    Runs ``expire_old_drafts`` first so stale pending drafts get cleaned
    up on every scan, then concatenates per-handle candidate lists.
    Order in the result follows watchlist order; ``pick_reply_candidate``
    re-sorts by engagement.
    """
    expire_old_drafts(state, now)
    all_candidates: List[Dict[str, Any]] = []
    for handle in watchlist:
        all_candidates.extend(await scan_handle(bsky_client, handle, state, now))
    return all_candidates


# ── Pick + stage ────────────────────────────────────────────────────────────

def pick_reply_candidate(
    candidates: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Pick the highest-engagement candidate from a scan, or None if empty.

    Sort key is ``engagement`` desc (replies + reposts). Ties broken by
    most-recently-indexed first — fresh posts deserve fresh replies.
    """
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: (int(c.get("engagement", 0) or 0), c.get("indexed_at", "")),
    )


def stage_draft_reply(
    candidate: Dict[str, Any],
    draft_reply: str,
    state: Dict[str, List[Dict[str, Any]]],
    now: datetime,
) -> Dict[str, Any]:
    """Append a new draft to ``state['pending']`` and return the stored entry.

    Caller is responsible for persisting via ``save_pending_replies``
    afterwards — staging mutates the in-memory state but does not write
    to the Gist. Separation matters because the scan loop may stage
    once per run and we want one Gist write per run, not per stage.
    """
    draft: Dict[str, Any] = {
        "id": new_draft_id(),
        "platform": candidate.get("platform", "bluesky"),
        "parent_author": candidate.get("parent_author", ""),
        "parent_post_uri": candidate.get("parent_post_uri", ""),
        "parent_text": candidate.get("parent_text", ""),
        "draft_reply": draft_reply,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=PROACTIVE_REPLY_DRAFT_EXPIRY_HOURS)).isoformat(),
    }
    state.setdefault("pending", []).append(draft)
    SafeLogger.info(
        "proactive_draft_staged",
        "New proactive-reply draft staged",
        draft_id=draft["id"],
        parent_author=draft["parent_author"],
        draft_length=len(draft_reply),
    )
    return draft


# ── Approve + reject ────────────────────────────────────────────────────────

def find_draft_in_pending(
    draft_id: str,
    state: Dict[str, List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Find a pending draft by ID, or oldest if ``draft_id == 'first'``.

    The ``first`` sentinel is the workflow input default — the
    common case is "approve whatever's at the top of the queue."
    Returns ``None`` when no match.
    """
    pending = state.get("pending", [])
    if not pending:
        return None
    if draft_id == "first":
        return min(pending, key=lambda d: d.get("generated_at", ""))
    for draft in pending:
        if draft.get("id") == draft_id:
            return draft
    return None


def _move_to_rejected(
    draft: Dict[str, Any],
    state: Dict[str, List[Dict[str, Any]]],
    now: datetime,
    reason: str,
) -> Dict[str, Any]:
    """Move a draft from pending → rejected. Returns the rejected entry.

    Defensive: if the draft has already been removed from pending, we
    still record it in rejected so the bookkeeping reflects intent.
    """
    rejected_entry = dict(draft)
    rejected_entry["rejected_at"] = now.isoformat()
    rejected_entry["rejected_reason"] = reason
    try:
        state.get("pending", []).remove(draft)
    except ValueError:
        pass
    state.setdefault("rejected", []).append(rejected_entry)
    SafeLogger.info(
        "proactive_draft_rejected",
        "Draft moved to rejected bucket",
        draft_id=draft.get("id", "?"),
        rejected_reason=reason,
    )
    return rejected_entry


def reject_draft(
    draft_id: str,
    state: Dict[str, List[Dict[str, Any]]],
    now: datetime,
    reason: str = "manual",
) -> Optional[Dict[str, Any]]:
    """Reject a pending draft, returning the rejected entry or None."""
    draft = find_draft_in_pending(draft_id, state)
    if draft is None:
        SafeLogger.error(
            "proactive_reject_draft_not_found",
            "No matching pending draft to reject",
            draft_id=draft_id,
        )
        return None
    return _move_to_rejected(draft, state, now, reason)


async def approve_draft(
    draft_id: str,
    bsky_client: Any,
    state: Dict[str, List[Dict[str, Any]]],
    now: datetime,
) -> Optional[Dict[str, Any]]:
    """Approve a pending draft: re-fetch parent, post reply, move to posted.

    Returns the posted entry on success, ``None`` on any failure.
    Failures auto-move the draft to ``rejected`` with a descriptive
    reason — the user sees what happened in the next state snapshot
    rather than having to dig through logs.

    Defensive design (per PLAN_engagement.md §4b):
      - Re-fetches the parent post via ``get_posts`` before sending.
        Aborts on 404 (parent deleted between scan and approval) or
        any fetch failure.
      - Uses the fresh ``cid`` from the re-fetch, not the staged
        candidate's. Parent edits invalidate the original cid; using
        stale cid → API error or wrong-thread reply.
      - Top-level parents only (filter rule), so ``root == parent`` in
        the reply ref.
    """
    draft = find_draft_in_pending(draft_id, state)
    if draft is None:
        SafeLogger.error(
            "proactive_approve_draft_not_found",
            "No matching pending draft to approve",
            draft_id=draft_id,
        )
        return None

    parent_uri = draft.get("parent_post_uri", "")
    try:
        response = await bsky_client.app.bsky.feed.get_posts(
            {"uris": [parent_uri]}
        )
    except Exception as e:
        reason = f"parent_refetch_failed: {type(e).__name__}: {str(e)[:100]}"
        SafeLogger.error(
            "proactive_approve_refetch_failed",
            "Could not re-fetch parent post; rejecting draft",
            draft_id=draft_id,
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        _move_to_rejected(draft, state, now, reason)
        return None

    posts = getattr(response, "posts", None) or []
    if not posts:
        SafeLogger.error(
            "proactive_approve_parent_gone",
            "Parent post no longer exists; rejecting draft",
            draft_id=draft_id,
            parent_post_uri=parent_uri,
        )
        _move_to_rejected(draft, state, now, "parent_gone")
        return None

    parent_view = posts[0]
    parent_cid = getattr(parent_view, "cid", None)
    fresh_parent_uri = getattr(parent_view, "uri", parent_uri)
    if not parent_cid:
        SafeLogger.error(
            "proactive_approve_no_cid",
            "Re-fetched parent has no cid; rejecting draft",
            draft_id=draft_id,
        )
        _move_to_rejected(draft, state, now, "parent_no_cid")
        return None

    reply_ref = {
        "parent": {"cid": parent_cid, "uri": fresh_parent_uri},
        "root":   {"cid": parent_cid, "uri": fresh_parent_uri},
    }

    try:
        posted = await bsky_client.send_post(
            text=draft["draft_reply"],
            reply_to=reply_ref,
        )
    except Exception as e:
        reason = f"post_failed: {type(e).__name__}: {str(e)[:100]}"
        SafeLogger.error(
            "proactive_approve_post_failed",
            "Bluesky send_post failed; rejecting draft",
            draft_id=draft_id,
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        _move_to_rejected(draft, state, now, reason)
        return None

    posted_uri = getattr(posted, "uri", "")
    try:
        state.get("pending", []).remove(draft)
    except ValueError:
        pass
    posted_entry = dict(draft)
    posted_entry["posted_at"] = now.isoformat()
    posted_entry["posted_uri"] = posted_uri
    state.setdefault("posted", []).append(posted_entry)

    SafeLogger.info(
        "proactive_approve_succeeded",
        "Draft posted to Bluesky as reply",
        draft_id=draft_id,
        parent_author=draft.get("parent_author", ""),
        posted_uri=posted_uri,
    )
    return posted_entry
