"""Telemetry persistence for feed health and (later) post metrics.

Phase 1 Step 2: `feed_health.json` — tracks per-feed fetch outcomes so we
can spot dead or drifting feeds at a glance. Same Gist-first, local-file-
fallback pattern as `seen_articles.json` / `replied_to.json`.

`post_metrics.json` is a Step 4 addition; kept out of this module until
then to keep the Step 2 diff tight.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.config import (
    FEED_HEALTH_FILE,
    FEED_HEALTH_RECENT_ATTEMPTS_LIMIT,
    POST_METRICS_CONTENT_PREVIEW_MAX_CHARS,
    POST_METRICS_FILE,
)
from src.logger import SafeLogger


@dataclass
class BroadcastResult:
    """Structured outcome of a single platform broadcast.

    `sent_uris` is populated whether delivery completed or stopped early —
    Step 3b uses it to avoid re-sending posts that already made the wire.
    For Bluesky this holds `at://…` URIs; for Mastodon, string status IDs.
    `client` is the authenticated Bluesky AsyncClient (None for Mastodon) so
    downstream stages (handle_interactions) can reuse the session.
    """
    client: Any = None
    sent_uris: List[str] = field(default_factory=list)
    error: Optional[Exception] = None


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


# ---------------------------------------------------------------------------
# Post metrics — Phase 1 Step 4 (record-on-broadcast). Step 5 will add the
# refresh + prune passes; this module already has the schema slots they
# need (metrics sub-object, posted_at) populated as zeros / None.
# ---------------------------------------------------------------------------

def load_post_metrics() -> Dict[str, Any]:
    """Same Gist-first, local-fallback pattern as load_feed_health."""
    from src.utils import _atomic_write_json, _load_gist_state, _load_json_with_repair

    default: Dict[str, Any] = {"posts": []}

    gist_data = _load_gist_state("post_metrics.json")
    if isinstance(gist_data, dict) and "posts" in gist_data:
        return gist_data

    data = _load_json_with_repair(POST_METRICS_FILE, lambda: default)
    if isinstance(data, dict) and "posts" in data:
        return data

    try:
        _atomic_write_json(POST_METRICS_FILE, default)
    except Exception as e:
        SafeLogger.warn(
            "post_metrics_repair_failed",
            "Failed to rewrite post_metrics.json with default shape",
            error_type=type(e).__name__,
        )
    return default


def save_post_metrics(data: Dict[str, Any]) -> None:
    """Persist post_metrics.json; Gist first, local file as fallback."""
    from src.utils import _atomic_write_json, _save_gist_state

    if _save_gist_state("post_metrics.json", data):
        return
    try:
        _atomic_write_json(POST_METRICS_FILE, data)
    except Exception as e:
        SafeLogger.error(
            "post_metrics_save_failed",
            "Failed to save post metrics",
            exception=e,
        )


def record_post_metric(
    post_metrics: Dict[str, Any],
    *,
    post_id: str,
    platform: str,
    mode: str,
    posted_at: str,
    content_preview: str,
    thread_position: int,
    topic: Optional[str] = None,
    source_domain: Optional[str] = None,
    pioneer_id: Optional[str] = None,
    had_image: bool = False,
    had_link_card: bool = False,
    language: Optional[str] = None,
) -> None:
    """Append a post-broadcast row to ``post_metrics`` (mutated in place).

    The ``metrics`` sub-object holds zeros until Step 5's refresh pass fills
    in live like/repost/reply counts. ``language`` is a placeholder slot —
    not currently captured at broadcast time; Phase 2 can backfill if the
    digest design needs per-language breakdown.
    """
    posts = post_metrics.setdefault("posts", [])
    preview = (content_preview or "").strip().replace("\n", " ")
    if len(preview) > POST_METRICS_CONTENT_PREVIEW_MAX_CHARS:
        preview = preview[: POST_METRICS_CONTENT_PREVIEW_MAX_CHARS - 1] + "…"
    posts.append({
        "post_id": post_id,
        "platform": platform,
        "mode": mode,
        "language": language,
        "posted_at": posted_at,
        "content_preview": preview,
        "topic": topic,
        "source_domain": source_domain,
        "pioneer_id": pioneer_id,
        "had_image": bool(had_image),
        "had_link_card": bool(had_link_card),
        "thread_position": thread_position,
        "metrics": {
            "likes": 0,
            "reposts": 0,
            "replies": 0,
            "fetched_at": None,
        },
    })
