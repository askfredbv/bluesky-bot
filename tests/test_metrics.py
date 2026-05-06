"""Tests for src.metrics — feed health telemetry (Phase 1 Step 2)."""
from __future__ import annotations

import pytest

from src import metrics
from src.config import FEED_HEALTH_RECENT_ATTEMPTS_LIMIT
from src.metrics import FeedFetchResult, record_feed_attempt


# ---------------------------------------------------------------------------
# FeedFetchResult
# ---------------------------------------------------------------------------

def test_feed_fetch_result_defaults_entries_to_empty_list():
    r = FeedFetchResult(url="https://x", ok=True, entries_total=0, entries_accepted=0)
    assert r.entries == []
    assert r.error_type is None


def test_feed_fetch_result_preserves_passed_entries():
    r = FeedFetchResult(
        url="https://x",
        ok=True,
        entries_total=2,
        entries_accepted=1,
        entries=[{"title": "a"}],
    )
    assert r.entries == [{"title": "a"}]


# ---------------------------------------------------------------------------
# record_feed_attempt
# ---------------------------------------------------------------------------

def test_record_feed_attempt_creates_entry_for_new_url():
    health = {"feeds": {}}
    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=True, entries_total=5, entries_accepted=3),
    )
    assert "https://a" in health["feeds"]
    entry = health["feeds"]["https://a"]
    assert entry["last_fetch_at"] is not None
    assert entry["last_ok_at"] is not None
    assert entry["last_accepted_at"] is not None
    assert len(entry["recent_attempts"]) == 1
    assert entry["recent_attempts"][0]["ok"] is True
    assert entry["recent_attempts"][0]["accepted"] == 3


def test_record_feed_attempt_does_not_touch_last_ok_on_failure():
    health = {"feeds": {}}
    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=False, entries_total=0, entries_accepted=0, error_type="ReadTimeout"),
    )
    entry = health["feeds"]["https://a"]
    assert entry["last_fetch_at"] is not None
    assert entry["last_ok_at"] is None
    assert entry["last_accepted_at"] is None
    assert entry["recent_attempts"][0]["error"] == "ReadTimeout"


def test_record_feed_attempt_only_bumps_accepted_when_entries_survive():
    """ok=True but zero accepted (e.g. all entries older than lookback) must
    leave last_accepted_at at None."""
    health = {"feeds": {}}
    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=True, entries_total=10, entries_accepted=0),
    )
    entry = health["feeds"]["https://a"]
    assert entry["last_ok_at"] is not None
    assert entry["last_accepted_at"] is None


def test_record_feed_attempt_appends_without_overwriting_prior_success():
    """A failure after prior success must preserve last_ok_at / last_accepted_at."""
    health = {"feeds": {}}
    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=True, entries_total=5, entries_accepted=2),
    )
    previous_ok_at = health["feeds"]["https://a"]["last_ok_at"]
    previous_accepted_at = health["feeds"]["https://a"]["last_accepted_at"]

    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=False, entries_total=0, entries_accepted=0, error_type="SSLError"),
    )
    entry = health["feeds"]["https://a"]
    assert entry["last_ok_at"] == previous_ok_at
    assert entry["last_accepted_at"] == previous_accepted_at
    assert len(entry["recent_attempts"]) == 2


def test_record_feed_attempt_trims_recent_attempts_to_limit():
    health = {"feeds": {}}
    for i in range(FEED_HEALTH_RECENT_ATTEMPTS_LIMIT + 5):
        record_feed_attempt(
            health,
            FeedFetchResult(url="https://a", ok=True, entries_total=i, entries_accepted=i),
        )
    attempts = health["feeds"]["https://a"]["recent_attempts"]
    assert len(attempts) == FEED_HEALTH_RECENT_ATTEMPTS_LIMIT
    # Oldest trimmed → most recent survive. accepted counts equal loop index,
    # so the surviving slice starts at index (total-limit) and ends at total-1.
    total = FEED_HEALTH_RECENT_ATTEMPTS_LIMIT + 5
    assert attempts[0]["accepted"] == total - FEED_HEALTH_RECENT_ATTEMPTS_LIMIT
    assert attempts[-1]["accepted"] == total - 1


# ---------------------------------------------------------------------------
# load_feed_health / save_feed_health
# ---------------------------------------------------------------------------

def test_load_feed_health_returns_default_when_no_gist_or_file(monkeypatch, tmp_path):
    monkeypatch.setattr("src.metrics.FEED_HEALTH_FILE", tmp_path / "missing.json")
    monkeypatch.setattr("src.utils._load_gist_state", lambda *_: None)
    assert metrics.load_feed_health() == {"feeds": {}}


def test_load_feed_health_prefers_gist(monkeypatch):
    payload = {"feeds": {"https://a": {"last_fetch_at": "2026-01-01T00:00:00+00:00"}}}
    monkeypatch.setattr("src.utils._load_gist_state", lambda name: payload if name == "feed_health.json" else None)
    assert metrics.load_feed_health() == payload


def test_save_feed_health_uses_gist_when_available(monkeypatch):
    calls = {}

    def fake_save_gist(name, data):
        calls["gist"] = (name, data)
        return True

    monkeypatch.setattr("src.utils._save_gist_state", fake_save_gist)
    metrics.save_feed_health({"feeds": {}})
    assert calls["gist"] == ("feed_health.json", {"feeds": {}})


def test_save_feed_health_falls_back_to_local_file(monkeypatch, tmp_path):
    monkeypatch.setattr("src.utils._save_gist_state", lambda *_: False)
    target = tmp_path / "feed_health.json"
    monkeypatch.setattr("src.metrics.FEED_HEALTH_FILE", target)

    metrics.save_feed_health({"feeds": {"https://a": {}}})
    assert target.exists()
    import json
    assert json.loads(target.read_text(encoding="utf-8")) == {"feeds": {"https://a": {}}}


# ---------------------------------------------------------------------------
# record_post_metric (Phase 1 Step 4)
# ---------------------------------------------------------------------------

def _record_kwargs(**overrides):
    """Sensible default args for record_post_metric tests; override per test."""
    base = dict(
        post_id="at://did:plc:fake/app.bsky.feed.post/abc",
        platform="bluesky",
        mode="curator",
        posted_at="2026-05-02T07:03:00+00:00",
        content_preview="A short observation about caching.",
        thread_position=0,
    )
    base.update(overrides)
    return base


def test_record_post_metric_appends_row_with_zeroed_metrics():
    post_metrics = {"posts": []}
    metrics.record_post_metric(post_metrics, **_record_kwargs())

    assert len(post_metrics["posts"]) == 1
    row = post_metrics["posts"][0]
    assert row["platform"] == "bluesky"
    assert row["mode"] == "curator"
    assert row["thread_position"] == 0
    # Metrics sub-object starts at zero — Step 5's refresh fills it later.
    assert row["metrics"] == {"likes": 0, "reposts": 0, "replies": 0, "fetched_at": None}


def test_record_post_metric_truncates_long_content_preview():
    post_metrics = {"posts": []}
    long_content = "x" * 500
    metrics.record_post_metric(
        post_metrics, **_record_kwargs(content_preview=long_content)
    )
    preview = post_metrics["posts"][0]["content_preview"]
    # Capped at POST_METRICS_CONTENT_PREVIEW_MAX_CHARS with ellipsis.
    assert len(preview) <= 80
    assert preview.endswith("…")


def test_record_post_metric_collapses_newlines_in_preview():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(content_preview="line one\n\nline two"),
    )
    preview = post_metrics["posts"][0]["content_preview"]
    assert "\n" not in preview
    assert "line one" in preview and "line two" in preview


def test_record_post_metric_handles_optional_fields_as_none():
    """topic, source_domain, pioneer_id, language are optional —
    rows should accept them as None rather than crashing."""
    post_metrics = {"posts": []}
    metrics.record_post_metric(post_metrics, **_record_kwargs())

    row = post_metrics["posts"][0]
    assert row["topic"] is None
    assert row["source_domain"] is None
    assert row["pioneer_id"] is None
    assert row["language"] is None
    assert row["had_image"] is False
    assert row["had_link_card"] is False


def test_record_post_metric_carries_full_context_when_provided():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(
            topic="LLMs",
            source_domain="openai.com",
            pioneer_id="sparck-jones-idf",
            had_image=True,
            had_link_card=True,
            language="English",
        ),
    )
    row = post_metrics["posts"][0]
    assert row["topic"] == "LLMs"
    assert row["source_domain"] == "openai.com"
    assert row["pioneer_id"] == "sparck-jones-idf"
    assert row["had_image"] is True
    assert row["had_link_card"] is True
    assert row["language"] == "English"


def test_record_post_metric_appends_to_existing_rows():
    post_metrics = {"posts": [{"post_id": "old"}]}
    metrics.record_post_metric(post_metrics, **_record_kwargs(post_id="new"))
    assert [p["post_id"] for p in post_metrics["posts"]] == ["old", "new"]


# ---------------------------------------------------------------------------
# Track A — formatting feature enrichment on record_post_metric
# ---------------------------------------------------------------------------

def test_record_post_metric_captures_length_chars_from_full_text():
    """length_chars reflects the FULL post text length, not the truncated preview."""
    post_metrics = {"posts": []}
    long_content = "A" * 250  # > preview cap, well within Bsky's 300
    metrics.record_post_metric(
        post_metrics, **_record_kwargs(content_preview=long_content)
    )
    row = post_metrics["posts"][0]
    assert row["length_chars"] == 250
    # And the preview is still truncated for display:
    assert len(row["content_preview"]) <= 80


def test_record_post_metric_counts_hashtags():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(content_preview="A note on #AI and #LLMs in production."),
    )
    assert post_metrics["posts"][0]["hashtag_count"] == 2


def test_record_post_metric_counts_questions():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(content_preview="Why does this matter? Because incentives matter."),
    )
    assert post_metrics["posts"][0]["question_count"] == 1


def test_record_post_metric_counts_emojis():
    post_metrics = {"posts": []}
    # Mix: pictograph (🚀 U+1F680), pictograph (💡 U+1F4A1), text (no emoji)
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(content_preview="🚀 launching today 💡 idea worth shipping"),
    )
    # Heuristic counts code points >= 0x1F000; both 🚀 and 💡 fall above it.
    assert post_metrics["posts"][0]["emoji_count"] == 2


def test_record_post_metric_emoji_count_zero_when_text_only():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(content_preview="A perfectly dry sentence with no emoji."),
    )
    assert post_metrics["posts"][0]["emoji_count"] == 0


def test_record_post_metric_carries_thread_length():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(thread_position=1, thread_length=3),
    )
    row = post_metrics["posts"][0]
    assert row["thread_position"] == 1
    assert row["thread_length"] == 3


def test_record_post_metric_thread_length_defaults_to_one():
    """A single-post thread is the default — never write 0."""
    post_metrics = {"posts": []}
    metrics.record_post_metric(post_metrics, **_record_kwargs())
    assert post_metrics["posts"][0]["thread_length"] == 1


def test_record_post_metric_buckets_morning_for_07_utc():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(posted_at="2026-05-05T07:03:00+00:00"),
    )
    assert post_metrics["posts"][0]["time_of_day_bucket"] == "morning"


def test_record_post_metric_buckets_afternoon_for_14_utc():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(posted_at="2026-05-05T14:33:00+00:00"),
    )
    assert post_metrics["posts"][0]["time_of_day_bucket"] == "afternoon"


def test_record_post_metric_time_of_day_handles_unparseable():
    post_metrics = {"posts": []}
    metrics.record_post_metric(
        post_metrics,
        **_record_kwargs(posted_at="not-a-date"),
    )
    assert post_metrics["posts"][0]["time_of_day_bucket"] is None


# ---------------------------------------------------------------------------
# load_post_metrics / save_post_metrics
# ---------------------------------------------------------------------------

def test_load_post_metrics_returns_default_shape_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("src.utils._load_gist_state", lambda *_: None)
    monkeypatch.setattr("src.metrics.POST_METRICS_FILE", tmp_path / "post_metrics.json")
    assert metrics.load_post_metrics() == {"posts": []}


def test_load_post_metrics_uses_gist_data_when_available(monkeypatch):
    payload = {"posts": [{"post_id": "at://x"}]}
    monkeypatch.setattr(
        "src.utils._load_gist_state",
        lambda name: payload if name == "post_metrics.json" else None,
    )
    assert metrics.load_post_metrics() == payload


def test_save_post_metrics_falls_back_to_local_file(monkeypatch, tmp_path):
    monkeypatch.setattr("src.utils._save_gist_state", lambda *_: False)
    target = tmp_path / "post_metrics.json"
    monkeypatch.setattr("src.metrics.POST_METRICS_FILE", target)

    metrics.save_post_metrics({"posts": [{"post_id": "x"}]})
    assert target.exists()
    import json
    assert json.loads(target.read_text(encoding="utf-8")) == {"posts": [{"post_id": "x"}]}


# ---------------------------------------------------------------------------
# Phase 1 Step 5 — should_refresh, prune_old_metrics, refresh_stale_metrics
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _row(*, posted_at_ago_hours=24.0, fetched_at_ago_hours=None, platform="bluesky", post_id="x"):
    posted_at = (NOW - timedelta(hours=posted_at_ago_hours)).isoformat()
    fetched_at = None
    if fetched_at_ago_hours is not None:
        fetched_at = (NOW - timedelta(hours=fetched_at_ago_hours)).isoformat()
    return {
        "post_id": post_id,
        "platform": platform,
        "posted_at": posted_at,
        "metrics": {"likes": 0, "reposts": 0, "replies": 0, "fetched_at": fetched_at},
    }


def test_should_refresh_skips_rows_under_floor():
    """A 1h-old post is under the 2h floor — no engagement signal yet."""
    assert metrics.should_refresh(_row(posted_at_ago_hours=1), NOW) is False


def test_should_refresh_picks_up_first_unfetched_after_floor():
    """A 3h-old post that has never been fetched needs its first refresh."""
    assert metrics.should_refresh(_row(posted_at_ago_hours=3), NOW) is True


def test_should_refresh_skips_recently_fetched_row():
    """Fetched 5h ago, well under the 20h staleness threshold."""
    assert metrics.should_refresh(
        _row(posted_at_ago_hours=24, fetched_at_ago_hours=5), NOW
    ) is False


def test_should_refresh_picks_up_stale_row():
    """Last fetch >20h ago — due for an update even if previously fetched."""
    assert metrics.should_refresh(
        _row(posted_at_ago_hours=48, fetched_at_ago_hours=21), NOW
    ) is True


def test_should_refresh_skips_aged_out_row():
    """A 31-day-old row is about to be pruned; don't burn API on it."""
    assert metrics.should_refresh(
        _row(posted_at_ago_hours=31 * 24, fetched_at_ago_hours=21), NOW
    ) is False


def test_should_refresh_handles_missing_posted_at():
    assert metrics.should_refresh({"post_id": "x", "metrics": {}}, NOW) is False


def test_should_refresh_handles_unparseable_posted_at():
    bad = {"post_id": "x", "posted_at": "not-a-date", "metrics": {}}
    assert metrics.should_refresh(bad, NOW) is False


# ---- prune_old_metrics ----

def test_prune_old_metrics_drops_rows_older_than_cap():
    pm = {"posts": [
        _row(posted_at_ago_hours=29 * 24, post_id="keep-29d"),
        _row(posted_at_ago_hours=30 * 24 + 1, post_id="drop-30d-and-change"),
        _row(posted_at_ago_hours=60 * 24, post_id="drop-60d"),
    ]}
    pruned = metrics.prune_old_metrics(pm, NOW)
    assert pruned == 2
    assert [r["post_id"] for r in pm["posts"]] == ["keep-29d"]


def test_prune_old_metrics_keeps_rows_with_unparseable_posted_at():
    """Rather than drop a real row due to a serialiser change, hold it."""
    pm = {"posts": [{"post_id": "weird", "posted_at": "huh", "metrics": {}}]}
    pruned = metrics.prune_old_metrics(pm, NOW)
    assert pruned == 0
    assert pm["posts"][0]["post_id"] == "weird"


def test_prune_old_metrics_handles_empty_posts():
    pm = {"posts": []}
    assert metrics.prune_old_metrics(pm, NOW) == 0
    assert pm["posts"] == []


# ---- refresh_stale_metrics ----

import pytest


@pytest.mark.asyncio
async def test_refresh_skips_rows_not_due():
    """No refresh-due rows → no API calls, no errors."""
    pm = {"posts": [_row(posted_at_ago_hours=1, post_id="too-fresh")]}
    counts = await metrics.refresh_stale_metrics(pm, bsky_client=None, mastodon_client=None, now=NOW)
    assert counts == {"bluesky": 0, "mastodon": 0, "skipped": 1, "errors": 0}


@pytest.mark.asyncio
async def test_refresh_updates_bluesky_rows_via_batched_api():
    """Two Bluesky rows are due → one batched get_posts call updates both."""
    pm = {"posts": [
        _row(posted_at_ago_hours=24, post_id="at://post/1"),
        _row(posted_at_ago_hours=24, post_id="at://post/2"),
    ]}

    class FakeEntry:
        def __init__(self, uri, like, repost, reply):
            self.uri = uri
            self.like_count = like
            self.repost_count = repost
            self.reply_count = reply

    class FakeResponse:
        posts = [
            FakeEntry("at://post/1", 5, 2, 1),
            FakeEntry("at://post/2", 0, 0, 0),
        ]

    captured_uris = []

    class FakeFeedNamespace:
        async def get_posts(self, params):
            captured_uris.extend(params["uris"])
            return FakeResponse()

    class FakeBskyNamespace:
        feed = FakeFeedNamespace()

    class FakeAppNamespace:
        bsky = FakeBskyNamespace()

    class FakeBskyClient:
        app = FakeAppNamespace()

    counts = await metrics.refresh_stale_metrics(
        pm, bsky_client=FakeBskyClient(), mastodon_client=None, now=NOW
    )

    assert counts["bluesky"] == 2
    assert counts["errors"] == 0
    assert set(captured_uris) == {"at://post/1", "at://post/2"}
    # Row metrics now hold the fetched values + a fetched_at stamp.
    by_id = {r["post_id"]: r for r in pm["posts"]}
    assert by_id["at://post/1"]["metrics"]["likes"] == 5
    assert by_id["at://post/1"]["metrics"]["reposts"] == 2
    assert by_id["at://post/2"]["metrics"]["fetched_at"] is not None


@pytest.mark.asyncio
async def test_refresh_updates_mastodon_rows_via_status_calls():
    pm = {"posts": [
        _row(posted_at_ago_hours=24, post_id="1000", platform="mastodon"),
    ]}

    class FakeMasto:
        def status(self, status_id):
            return {"favourites_count": 7, "reblogs_count": 3, "replies_count": 2}

    counts = await metrics.refresh_stale_metrics(
        pm, bsky_client=None, mastodon_client=FakeMasto(), now=NOW
    )
    assert counts["mastodon"] == 1
    assert pm["posts"][0]["metrics"]["likes"] == 7
    assert pm["posts"][0]["metrics"]["reposts"] == 3
    assert pm["posts"][0]["metrics"]["replies"] == 2


@pytest.mark.asyncio
async def test_refresh_does_not_let_one_platform_fail_block_the_other():
    """A Bluesky API outage must not prevent the Mastodon refresh."""
    pm = {"posts": [
        _row(posted_at_ago_hours=24, post_id="at://post/1", platform="bluesky"),
        _row(posted_at_ago_hours=24, post_id="1000", platform="mastodon"),
    ]}

    class BrokenBsky:
        class app:
            class bsky:
                class feed:
                    @staticmethod
                    async def get_posts(_params):
                        raise RuntimeError("bluesky down")

    class FakeMasto:
        def status(self, status_id):
            return {"favourites_count": 1, "reblogs_count": 0, "replies_count": 0}

    counts = await metrics.refresh_stale_metrics(
        pm, bsky_client=BrokenBsky(), mastodon_client=FakeMasto(), now=NOW
    )
    assert counts["bluesky"] == 0
    assert counts["mastodon"] == 1
    assert counts["errors"] >= 1
