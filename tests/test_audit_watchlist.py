"""Unit tests for the pure helpers in `scripts/audit_watchlist.py`.

The fetcher / orchestration layer is I/O-heavy and is exercised manually by
running the script. These tests cover the scoring math, statement detection,
HTML stripping, and markdown rendering shape — the parts where regressions
would silently rank the wrong handle highest.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.audit_watchlist import (
    HandleAudit,
    NormalisedPost,
    _ERROR_MAX_CHARS,
    _format_error,
    cadence_score,
    engagement_substrate_score,
    render_markdown,
    reply_opportunity_score,
    score_handle,
    strip_html,
    topic_fit_score,
    voice_compat_score,
)


# ---------------------------------------------------------------------------
# _format_error
# ---------------------------------------------------------------------------

def test_format_error_truncates_huge_messages():
    """A 200KB HTML body in an exception must not bloat the audit file."""
    exc = RuntimeError("<!DOCTYPE html>" + "x" * 100_000)
    formatted = _format_error(exc)
    # Type prefix + truncated msg + ellipsis — must be capped at a sane size.
    assert len(formatted) <= _ERROR_MAX_CHARS + 20
    assert formatted.startswith("RuntimeError:")
    assert formatted.endswith("…")


def test_format_error_collapses_newlines():
    exc = ValueError("line one\n\nline two")
    assert "\n" not in _format_error(exc)


def test_format_error_short_message_passes_through():
    exc = KeyError("missing")
    assert _format_error(exc) == "KeyError: 'missing'"


def test_format_error_empty_message_returns_just_type():
    exc = RuntimeError()
    assert _format_error(exc) == "RuntimeError"


# ---------------------------------------------------------------------------
# strip_html
# ---------------------------------------------------------------------------

def test_strip_html_removes_tags_and_collapses_whitespace():
    html = "<p>Hello <b>world</b></p><p>Second paragraph.</p>"
    assert strip_html(html) == "Hello world Second paragraph."


def test_strip_html_handles_empty_and_none_safely():
    assert strip_html("") == ""
    assert strip_html(None) == ""  # type: ignore[arg-type]


def test_strip_html_preserves_word_boundaries_across_br():
    assert strip_html("line one<br>line two") == "line one line two"


# ---------------------------------------------------------------------------
# topic_fit_score
# ---------------------------------------------------------------------------

def test_topic_fit_zero_for_empty_input():
    assert topic_fit_score([]) == 0.0


def test_topic_fit_full_when_every_post_mentions_topic_keyword():
    posts = [
        "Just shipped a new prompt for Claude.",
        "Watching nvidia chip benchmarks.",
        "Reasoning models are getting better.",
    ]
    assert topic_fit_score(posts) == 100.0


def test_topic_fit_partial_when_some_posts_off_topic():
    posts = [
        "Tried a new transformer architecture.",
        "Went hiking this weekend.",
        "Quiet morning, no thoughts.",
        "Open-source culture matters.",
    ]
    # 2 of 4 hit topics → 50%
    assert topic_fit_score(posts) == 50.0


# ---------------------------------------------------------------------------
# voice_compat_score
# ---------------------------------------------------------------------------

def test_voice_compat_full_when_no_offences():
    posts = [
        "A quiet observation about caching.",
        "Shipped a small refactor today.",
    ]
    assert voice_compat_score(posts) == 100.0


def test_voice_compat_zero_when_every_post_has_hype():
    posts = [
        "This is amazing.",
        "Truly groundbreaking work here.",
    ]
    assert voice_compat_score(posts) == 0.0


def test_voice_compat_zero_when_every_post_is_reader_bait():
    posts = [
        "Saw this benchmark — what do you think?",
        "Tried this new model. Have you tried it?",
    ]
    assert voice_compat_score(posts) == 0.0


def test_voice_compat_partial_with_mixed_window():
    posts = [
        "Calm note about an obscure paper.",
        "This is incredible.",
        "Something else.",
        "What's your take on it?",
    ]
    # 2 offences out of 4 → 50%
    assert voice_compat_score(posts) == 50.0


# ---------------------------------------------------------------------------
# reply_opportunity_score
# ---------------------------------------------------------------------------

def _post(text: str, *, is_repost: bool = False) -> NormalisedPost:
    return NormalisedPost(
        text=text, timestamp=0.0, likes=0, reposts=0, replies=0,
        is_repost=is_repost,
    )


def test_reply_opportunity_zero_for_only_reposts():
    assert reply_opportunity_score([_post("Some text", is_repost=True)]) == 0.0


def test_reply_opportunity_zero_for_bare_link():
    assert reply_opportunity_score([_post("https://example.com")]) == 0.0


def test_reply_opportunity_zero_for_short_post():
    assert reply_opportunity_score([_post("hi")]) == 0.0


def test_reply_opportunity_full_for_substantive_statements():
    posts = [
        _post("A real thought about cache invalidation in distributed systems."),
        _post("Quiet observation: most monitoring dashboards measure the wrong thing."),
    ]
    assert reply_opportunity_score(posts) == 100.0


def test_reply_opportunity_strips_urls_when_measuring_length():
    # Body without URL is too short to count.
    short_with_link = _post("Big news! https://example.com/very/long/url/path")
    assert reply_opportunity_score([short_with_link]) == 0.0


# ---------------------------------------------------------------------------
# cadence_score
# ---------------------------------------------------------------------------

def test_cadence_zero_when_no_posts_in_window():
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    old = NormalisedPost(
        text="x", timestamp=(now.timestamp() - 60 * 86400),
        likes=0, reposts=0, replies=0, is_repost=False,
    )
    score, per_week = cadence_score([old], now)
    assert (score, per_week) == (0.0, 0.0)


def test_cadence_clamps_at_100_for_high_volume():
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    # 30 posts in last 14 days = 15/wk → clamp to 100
    posts = [
        NormalisedPost(
            text=f"post {i}",
            timestamp=now.timestamp() - i * 86400 / 2,  # spread within 15 days
            likes=0, reposts=0, replies=0, is_repost=False,
        )
        for i in range(30)
    ]
    # Filter to within window in helper itself
    score, per_week = cadence_score(posts, now)
    assert score == 100.0
    assert per_week >= 10.0


def test_cadence_excludes_reposts_from_cadence_count():
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    own = NormalisedPost(
        text="real", timestamp=now.timestamp() - 3 * 86400,
        likes=0, reposts=0, replies=0, is_repost=False,
    )
    boost = NormalisedPost(
        text="boosted", timestamp=now.timestamp() - 4 * 86400,
        likes=0, reposts=0, replies=0, is_repost=True,
    )
    score, per_week = cadence_score([own, boost], now)
    # 1 own post in 14 days = 0.5/wk → score = 5
    assert per_week == pytest.approx(0.5, abs=0.01)
    assert score == pytest.approx(5.0, abs=0.5)


# ---------------------------------------------------------------------------
# engagement_substrate_score
# ---------------------------------------------------------------------------

def test_engagement_substrate_zero_when_no_own_posts():
    boost = NormalisedPost(
        text="x", timestamp=0.0, likes=10, reposts=10, replies=10, is_repost=True,
    )
    score, avg = engagement_substrate_score([boost])
    assert (score, avg) == (0.0, 0.0)


def test_engagement_substrate_clamps_at_100():
    posts = [
        NormalisedPost(
            text="a", timestamp=0.0, likes=0,
            reposts=10, replies=10, is_repost=False,
        ),
    ]
    score, avg = engagement_substrate_score(posts)
    assert score == 100.0
    assert avg == 20.0


def test_engagement_substrate_likes_dont_count():
    """Likes alone shouldn't move the needle — only replies + reposts do."""
    likes_only = NormalisedPost(
        text="a", timestamp=0.0, likes=1000,
        reposts=0, replies=0, is_repost=False,
    )
    score, avg = engagement_substrate_score([likes_only])
    assert score == 0.0
    assert avg == 0.0


# ---------------------------------------------------------------------------
# Aggregate via score_handle
# ---------------------------------------------------------------------------

def test_score_handle_populates_all_fields():
    now = datetime(2026, 4, 27, tzinfo=timezone.utc)
    audit = HandleAudit(platform="bluesky", handle="example.bsky.social", posts=[
        NormalisedPost(
            text="A calm note about LLM evaluation methodology over time.",
            timestamp=now.timestamp() - 86400,
            likes=5, reposts=2, replies=1, is_repost=False,
        ),
        NormalisedPost(
            text="Tried a new prompt format for Claude — interesting results.",
            timestamp=now.timestamp() - 2 * 86400,
            likes=3, reposts=1, replies=2, is_repost=False,
        ),
    ])
    score_handle(audit, now)

    assert audit.topic_fit > 0
    assert audit.voice_compat == 100.0
    assert audit.reply_opportunity == 100.0
    assert audit.cadence > 0
    assert audit.engagement_substrate > 0
    assert 0 <= audit.aggregate <= 100


# ---------------------------------------------------------------------------
# Markdown rendering — shape only, not content
# ---------------------------------------------------------------------------

def test_render_markdown_orders_handles_by_aggregate_descending():
    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
    a = HandleAudit(platform="bluesky", handle="low.bsky.social",
                    topic_fit=10, voice_compat=10, reply_opportunity=10,
                    cadence=10, engagement_substrate=10)
    b = HandleAudit(platform="bluesky", handle="high.bsky.social",
                    topic_fit=90, voice_compat=90, reply_opportunity=90,
                    cadence=90, engagement_substrate=90)
    md = render_markdown([a, b], now)

    pos_high = md.find("high.bsky.social")
    pos_low = md.find("low.bsky.social")
    assert 0 < pos_high < pos_low, "high-aggregate handle must come first"


def test_render_markdown_lists_skipped_handles_separately():
    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
    skipped = HandleAudit(
        platform="mastodon", handle="missing@nowhere",
        error="federation lookup empty",
    )
    md = render_markdown([skipped], now)
    assert "## Skipped" in md
    assert "missing@nowhere" in md
    assert "federation lookup empty" in md


def test_render_markdown_includes_low_cadence_warning():
    now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
    a = HandleAudit(
        platform="bluesky", handle="quiet.bsky.social",
        topic_fit=50, voice_compat=80, reply_opportunity=60,
        cadence=10, posts_per_week=1.0, engagement_substrate=20,
    )
    md = render_markdown([a], now)
    assert "⚠️" in md
