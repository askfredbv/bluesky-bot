"""Telemetry persistence for feed health and (later) post metrics.

Phase 1 Step 2: `feed_health.json` — tracks per-feed fetch outcomes so we
can spot dead or drifting feeds at a glance. Same Gist-first, local-file-
fallback pattern as `seen_articles.json` / `replied_to.json`.

`post_metrics.json` is a Step 4 addition; kept out of this module until
then to keep the Step 2 diff tight.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.config import FEED_HEALTH_FILE, FEED_HEALTH_RECENT_ATTEMPTS_LIMIT
from src.logger import SafeLogger


@dataclass
class FeedFetchResult:
    """Structured outcome of a single feed fetch — replaces the bare list.

    `entries` is the accepted-and-normalised items that `fetch_news`
    aggregates; the other fields drive `feed_health.json`.
    """
    url: str
    ok: bool
    entries_total: int
    entries_accepted: int
    error_type: Optional[str] = None
    entries: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.entries is None:
            self.entries = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_feed_health() -> Dict[str, Any]:
    """Load feed_health.json via the same Gist/local pattern as seen_articles.

    Imports are local to avoid a circular dependency with `src.utils`
    (utils imports from this module for `FeedFetchResult`).
    """
    from src.utils import _atomic_write_json, _load_gist_state, _load_json_with_repair

    default: Dict[str, Any] = {"feeds": {}}

    gist_data = _load_gist_state("feed_health.json")
    if isinstance(gist_data, dict) and "feeds" in gist_data:
        return gist_data

    data = _load_json_with_repair(FEED_HEALTH_FILE, lambda: default)
    if isinstance(data, dict) and "feeds" in data:
        return data

    # Unexpected shape — repair to default. This write is safe because the
    # file was either missing or corrupt; atomic_write replaces atomically.
    try:
        _atomic_write_json(FEED_HEALTH_FILE, default)
    except Exception as e:
        SafeLogger.warn(
            "feed_health_repair_failed",
            "Failed to rewrite feed_health.json with default shape",
            error_type=type(e).__name__,
        )
    return default


def save_feed_health(data: Dict[str, Any]) -> None:
    """Persist feed_health.json; Gist first, local file as fallback."""
    from src.utils import _atomic_write_json, _save_gist_state

    if _save_gist_state("feed_health.json", data):
        return
    try:
        _atomic_write_json(FEED_HEALTH_FILE, data)
    except Exception as e:
        SafeLogger.error(
            "feed_health_save_failed",
            "Failed to save feed health",
            exception=e,
        )


def record_feed_attempt(feed_health: Dict[str, Any], result: "FeedFetchResult") -> None:
    """Append a fetch attempt to `feed_health` (mutated in place).

    Rolls `recent_attempts` to FEED_HEALTH_RECENT_ATTEMPTS_LIMIT entries.
    Bumps `last_fetch_at` always, `last_ok_at` on any successful request,
    `last_accepted_at` only when at least one entry survived lookback and
    normalisation (the strongest signal a feed is actually producing value).
    """
    feeds = feed_health.setdefault("feeds", {})
    entry = feeds.setdefault(result.url, {
        "last_fetch_at": None,
        "last_ok_at": None,
        "last_accepted_at": None,
        "recent_attempts": [],
    })

    now = _now_iso()
    entry["last_fetch_at"] = now
    if result.ok:
        entry["last_ok_at"] = now
    if result.entries_accepted > 0:
        entry["last_accepted_at"] = now

    attempts = entry.setdefault("recent_attempts", [])
    attempts.append({
        "at": now,
        "ok": result.ok,
        "accepted": result.entries_accepted,
        "total": result.entries_total,
        "error": result.error_type,
    })
    # Trim from the left so the most recent survive.
    if len(attempts) > FEED_HEALTH_RECENT_ATTEMPTS_LIMIT:
        del attempts[:-FEED_HEALTH_RECENT_ATTEMPTS_LIMIT]
