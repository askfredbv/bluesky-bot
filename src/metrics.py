"""Telemetry persistence for feed health and (later) post metrics.

Phase 1 Step 2: `feed_health.json` — tracks per-feed fetch outcomes so we
can spot dead or drifting feeds at a glance. Same Gist-first, local-file-
fallback pattern as `seen_articles.json` / `replied_to.json`.

`post_metrics.json` is a Step 4 addition; kept out of this module until
then to keep the Step 2 diff tight.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.config import (
    FEED_HEALTH_FILE,
    FEED_HEALTH_RECENT_ATTEMPTS_LIMIT,
    GROWTH_FILE,
    POST_METRICS_BLUESKY_BATCH_SIZE,
    POST_METRICS_CONTENT_PREVIEW_MAX_CHARS,
    POST_METRICS_FILE,
    POST_METRICS_MAX_AGE_DAYS,
    POST_METRICS_REFRESH_FLOOR_HOURS,
    POST_METRICS_REFRESH_STALE_HOURS,
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


# Track A formatting-feature helpers — pure, no I/O. Used by record_post_metric
# to enrich each row with the breakdowns Phase 2's digest will want without
# having to backfill the schema later.
_HASHTAG_RE = re.compile(r"#\w+")


def _count_emojis(text: str) -> int:
    """Count Unicode pictographs / emoji-range glyphs in ``text``.

    Heuristic: we count code points >= 0x1F000, which covers the Misc-Symbols-
    and-Pictographs / Emoticons / Transport-and-Map / Supplemental-Symbols
    blocks where post-style emojis live. Skin-tone modifiers and ZWJ glue
    code points get counted as part of the pictograph cluster they belong
    to, so a complex emoji like family-of-four reads as ~4 instead of 1.
    For relative-rate measurement (the digest's actual question) that
    over-count is acceptable — alternative would be a third-party `emoji`
    package, which is overhead we don't need yet.
    """
    return sum(1 for c in text if ord(c) >= 0x1F000)


def _bucket_time_of_day(posted_at: Optional[str]) -> Optional[str]:
    """Return ``"morning"`` for UTC hour < 12, ``"afternoon"`` otherwise.

    Bot's two daily runs are at 07:00 / 14:30 UTC in summer (CEST) and
    08:00 / 15:30 UTC in winter (CET) — both buckets cleanly separable
    by the noon-UTC split. Returns None if posted_at is unparseable.
    """
    parsed = _parse_iso(posted_at)
    if parsed is None:
        return None
    return "morning" if parsed.hour < 12 else "afternoon"


def record_post_metric(
    post_metrics: Dict[str, Any],
    *,
    post_id: str,
    platform: str,
    mode: str,
    posted_at: str,
    content_preview: str,
    thread_position: int,
    thread_length: int = 1,
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

    Track A (Phase 1 post-pipeline enrichment) adds derived formatting
    features computed at record time: ``length_chars``, ``thread_length``,
    ``emoji_count``, ``hashtag_count``, ``question_count``, and
    ``time_of_day_bucket``. These let Phase 2's digest answer "do hashtags
    correlate with engagement?" without backfilling the schema.
    """
    posts = post_metrics.setdefault("posts", [])
    full_text = (content_preview or "").strip().replace("\n", " ")
    length_chars = len(full_text)
    preview = full_text
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
        "thread_length": int(thread_length) if thread_length else 1,
        "length_chars": length_chars,
        "emoji_count": _count_emojis(full_text),
        "hashtag_count": len(_HASHTAG_RE.findall(full_text)),
        "question_count": full_text.count("?"),
        "time_of_day_bucket": _bucket_time_of_day(posted_at),
        "metrics": {
            "likes": 0,
            "reposts": 0,
            "replies": 0,
            "fetched_at": None,
        },
    })


# ---------------------------------------------------------------------------
# Phase 1 Step 5 — refresh stale rows + prune old ones.
#
# Refresh policy: hit a row only when posted_at is at least
# POST_METRICS_REFRESH_FLOOR_HOURS old (skip 0–2h dead zone with no signal),
# fetched_at is None or older than POST_METRICS_REFRESH_STALE_HOURS (so each
# row gets refreshed at least once per 24h with 2 runs/day), and posted_at
# is within POST_METRICS_MAX_AGE_DAYS (don't burn API on rows about to be
# pruned). Steady-state expectation: ~10–20 API calls per run.
# ---------------------------------------------------------------------------

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Tolerant ISO-8601 parser; returns None on bad input.

    Handles the trailing-Z form some serialisers produce as well as
    timezone-aware ISO strings the bot writes natively.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def should_refresh(post_row: Dict[str, Any], now: datetime) -> bool:
    """Pure decision: does this row need a metrics refresh on this run?"""
    posted_at = _parse_iso(post_row.get("posted_at"))
    if posted_at is None:
        return False
    age = now - posted_at
    if age < timedelta(hours=POST_METRICS_REFRESH_FLOOR_HOURS):
        return False
    if age >= timedelta(days=POST_METRICS_MAX_AGE_DAYS):
        return False
    fetched_at = _parse_iso(post_row.get("metrics", {}).get("fetched_at"))
    if fetched_at is None:
        return True
    return (now - fetched_at) >= timedelta(hours=POST_METRICS_REFRESH_STALE_HOURS)


def prune_old_metrics(post_metrics: Dict[str, Any], now: datetime) -> int:
    """Drop rows whose posted_at is older than POST_METRICS_MAX_AGE_DAYS.

    Returns the count of pruned rows. Mutates ``post_metrics`` in place.
    Rows with unparseable posted_at are kept (we'd rather hold a stale
    row than drop a real one due to a serialiser change).
    """
    posts = post_metrics.get("posts", [])
    cutoff = now - timedelta(days=POST_METRICS_MAX_AGE_DAYS)
    keep: List[Dict[str, Any]] = []
    pruned = 0
    for row in posts:
        posted_at = _parse_iso(row.get("posted_at"))
        if posted_at is not None and posted_at < cutoff:
            pruned += 1
            continue
        keep.append(row)
    post_metrics["posts"] = keep
    return pruned


def _chunked(seq: List[Any], size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


async def refresh_stale_metrics(
    post_metrics: Dict[str, Any],
    bsky_client: Any,
    mastodon_client: Any,
    now: datetime,
) -> Dict[str, int]:
    """Update ``metrics`` sub-objects for rows that are due a refresh.

    Returns a small counts dict suitable for logging: ``{bluesky, mastodon,
    skipped, errors}``. Errors are caught per-platform so a Bluesky outage
    cannot block the Mastodon refresh and vice versa.
    """
    posts = post_metrics.get("posts", [])
    counts = {"bluesky": 0, "mastodon": 0, "skipped": 0, "errors": 0}

    bsky_due: List[Dict[str, Any]] = []
    mastodon_due: List[Dict[str, Any]] = []
    for row in posts:
        if not should_refresh(row, now):
            counts["skipped"] += 1
            continue
        if row.get("platform") == "bluesky":
            bsky_due.append(row)
        elif row.get("platform") == "mastodon":
            mastodon_due.append(row)

    fetched_iso = now.isoformat()

    # ---- Bluesky: batched ≤25 URIs per call ----
    if bsky_due and bsky_client is not None:
        by_uri = {row["post_id"]: row for row in bsky_due if row.get("post_id")}
        try:
            for chunk in _chunked(list(by_uri.keys()), POST_METRICS_BLUESKY_BATCH_SIZE):
                response = await bsky_client.app.bsky.feed.get_posts({"uris": chunk})
                for entry in getattr(response, "posts", []) or []:
                    uri = getattr(entry, "uri", None)
                    row = by_uri.get(uri)
                    if row is None:
                        continue
                    row["metrics"] = {
                        "likes": int(getattr(entry, "like_count", 0) or 0),
                        "reposts": int(getattr(entry, "repost_count", 0) or 0),
                        "replies": int(getattr(entry, "reply_count", 0) or 0),
                        "fetched_at": fetched_iso,
                    }
                    counts["bluesky"] += 1
        except Exception as e:
            counts["errors"] += 1
            SafeLogger.warn(
                "post_metrics_bluesky_refresh_failed",
                "Bluesky metrics refresh raised",
                error_type=type(e).__name__,
                error_msg=str(e)[:200],
            )

    # ---- Mastodon: one call per row, in a thread to avoid blocking the loop ----
    if mastodon_due and mastodon_client is not None:
        import asyncio
        for row in mastodon_due:
            status_id = row.get("post_id")
            if not status_id:
                continue
            try:
                status = await asyncio.to_thread(mastodon_client.status, status_id)
                row["metrics"] = {
                    "likes": int(status.get("favourites_count", 0) or 0),
                    "reposts": int(status.get("reblogs_count", 0) or 0),
                    "replies": int(status.get("replies_count", 0) or 0),
                    "fetched_at": fetched_iso,
                }
                counts["mastodon"] += 1
            except Exception as e:
                counts["errors"] += 1
                SafeLogger.warn(
                    "post_metrics_mastodon_refresh_failed",
                    "Mastodon metrics refresh raised",
                    error_type=type(e).__name__,
                    error_msg=str(e)[:200],
                    status_id=status_id,
                )

    return counts


# ---------------------------------------------------------------------------
# Growth telemetry (2026-05-08) — per-run follower-count snapshots.
#
# Until 2026-05-08 the bot measured per-post engagement (likes/reposts/replies)
# but had no telemetry on the actual Option 1 success metric: follower count.
# Each run appends one snapshot per platform to growth.json so the weekly
# delta becomes visible without reading the platforms' UIs.
#
# Schema is intentionally append-only and uncapped; at 2 runs/day × 2 platforms
# the file grows ~4 rows/day, which is fine indefinitely. Trim/prune logic can
# be added if it ever matters.
# ---------------------------------------------------------------------------

def load_growth() -> Dict[str, Any]:
    """Load growth.json via the same Gist/local pattern as feed_health."""
    from src.utils import _atomic_write_json, _load_gist_state, _load_json_with_repair

    default: Dict[str, Any] = {"snapshots": []}

    gist_data = _load_gist_state("growth.json")
    if isinstance(gist_data, dict) and "snapshots" in gist_data:
        return gist_data

    data = _load_json_with_repair(GROWTH_FILE, lambda: default)
    if isinstance(data, dict) and "snapshots" in data:
        return data

    try:
        _atomic_write_json(GROWTH_FILE, default)
    except Exception as e:
        SafeLogger.warn(
            "growth_repair_failed",
            "Failed to rewrite growth.json with default shape",
            error_type=type(e).__name__,
        )
    return default


def save_growth(data: Dict[str, Any]) -> None:
    """Persist growth.json; Gist first, local file as fallback."""
    from src.utils import _atomic_write_json, _save_gist_state

    if _save_gist_state("growth.json", data):
        return
    try:
        _atomic_write_json(GROWTH_FILE, data)
    except Exception as e:
        SafeLogger.error(
            "growth_save_failed",
            "Failed to save growth snapshot",
            exception=e,
        )


def record_follower_snapshot(
    growth: Dict[str, Any],
    *,
    platform: str,
    followers_count: int,
    follows_count: Optional[int] = None,
    posts_count: Optional[int] = None,
    snapshot_at: Optional[str] = None,
) -> None:
    """Append a follower-count snapshot. Mutates ``growth`` in place.

    ``follows_count`` and ``posts_count`` are captured when the platform's
    API exposes them in the same call — purely informational, not required.
    The weekly delta in ``followers_count`` is the Option 1 success metric.
    """
    snapshots = growth.setdefault("snapshots", [])
    snapshots.append({
        "at": snapshot_at or _now_iso(),
        "platform": platform,
        "followers": int(followers_count),
        "follows": int(follows_count) if follows_count is not None else None,
        "posts": int(posts_count) if posts_count is not None else None,
    })
