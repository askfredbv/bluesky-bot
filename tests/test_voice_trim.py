"""Tests for the v4.14 defensive voice-trim helpers in src.agents.

These helpers are the last line of defence: the prompt tells the model to cap
hashtags at 2, avoid reader-bait questions, and stay under 300 chars, but the
model drifts. The trims here catch the drift before posting.
"""
import pytest

from src.agents import (
    MAX_HASHTAGS_PER_POST,
    _apply_voice_trim,
    _ends_with_reader_bait_question,
    _safe_truncate_post,
    _strip_excess_hashtags,
    _strip_trailing_question_bait,
)
from src.config import MAX_POST_LENGTH_BSKY


# ── _strip_excess_hashtags ────────────────────────────────────────────────────

def test_strip_excess_hashtags_below_cap_unchanged():
    """Posts at or below the cap pass through untouched."""
    text = "A quiet observation about scaling. #AI #Scaling"
    assert _strip_excess_hashtags(text) == text


def test_strip_excess_hashtags_zero_hashtags_unchanged():
    """The default voice has no hashtags — must not break that case."""
    text = "Notes on the new model release without any tags at all."
    assert _strip_excess_hashtags(text) == text


def test_strip_excess_hashtags_drops_excess_keeps_first():
    """First MAX_HASHTAGS_PER_POST tags win; the rest are removed."""
    text = "Thinking about agents. #AI #Agents #LLM #ML #Tech"
    result = _strip_excess_hashtags(text)
    assert result.count("#") == MAX_HASHTAGS_PER_POST
    assert "#AI" in result and "#Agents" in result
    assert "#LLM" not in result and "#ML" not in result and "#Tech" not in result


def test_strip_excess_hashtags_collapses_whitespace():
    """Removing tags shouldn't leave double-spaces."""
    text = "One #a #b #c two"
    result = _strip_excess_hashtags(text)
    assert "  " not in result


# ── _ends_with_reader_bait_question / _strip_trailing_question_bait ──────────

def test_ends_with_reader_bait_question_detects_thoughts():
    assert _ends_with_reader_bait_question("Long context windows changed everything. Thoughts?") is True


def test_ends_with_reader_bait_question_clean_post():
    assert _ends_with_reader_bait_question("Long context windows changed everything.") is False


def test_strip_trailing_question_bait_removes_final_question():
    text = "Long context windows changed everything. What do you think?"
    result = _strip_trailing_question_bait(text)
    assert "what do you think" not in result.lower()
    assert "Long context windows changed everything." in result


def test_strip_trailing_question_bait_preserves_clean_post():
    text = "A single observation that stands on its own."
    assert _strip_trailing_question_bait(text) == text


def test_strip_trailing_question_bait_keeps_post_when_trim_too_short():
    """If trimming would leave the post under the validation floor, keep original."""
    text = "Short. What do you think?"
    # Trimmed would be "Short." (6 chars) — well under MIN_POST_CHARS_FOR_VALIDATION
    assert _strip_trailing_question_bait(text) == text


def test_strip_trailing_question_bait_one_sentence_unchanged():
    """A single-sentence post that happens to be a question can't be safely trimmed."""
    text = "What do you think about distributed consensus protocols today?"
    assert _strip_trailing_question_bait(text) == text


# ── _safe_truncate_post ──────────────────────────────────────────────────────

def test_safe_truncate_post_under_limit_unchanged():
    text = "Short post."
    assert _safe_truncate_post(text) == text


def test_safe_truncate_post_cuts_at_word_boundary():
    text = "word " * 100  # 500 chars, plenty over 300
    result = _safe_truncate_post(text)
    assert len(result) <= MAX_POST_LENGTH_BSKY
    # Should not end mid-word
    assert not result.endswith("wor")
    assert result.endswith("word") or result.endswith("…")


def test_safe_truncate_post_hard_cuts_when_no_boundary():
    """A single 400-char token has no whitespace — must hard-cut with ellipsis."""
    text = "a" * 400
    result = _safe_truncate_post(text)
    assert len(result) <= MAX_POST_LENGTH_BSKY
    assert result.endswith("…")


def test_safe_truncate_post_respects_custom_limit():
    text = "one two three four five six seven eight"
    result = _safe_truncate_post(text, limit=15)
    assert len(result) <= 15


# ── _apply_voice_trim (integration) ──────────────────────────────────────────

def test_apply_voice_trim_runs_all_three_passes():
    """Question + excess hashtags + overflow all addressed in one pass.

    Question bait is always at the END of the post (that's where the model
    drops it), so we order the trims accordingly: padding first, then tags,
    then the bait that needs trimming.
    """
    padding = "Some genuine observation about the topic. " * 5  # ~210 chars
    posts = [
        f"{padding}#a #b #c #d What do you think?"
    ]
    result = _apply_voice_trim(posts)
    out = result[0]
    assert len(out) <= MAX_POST_LENGTH_BSKY
    assert out.count("#") <= MAX_HASHTAGS_PER_POST
    assert "what do you think" not in out.lower()


def test_apply_voice_trim_clean_posts_unchanged():
    posts = [
        "A quiet observation. #AI",
        "Notes on the morning's reading.",
    ]
    assert _apply_voice_trim(posts) == posts


def test_apply_voice_trim_preserves_post_count():
    """Trimming must never drop or duplicate posts in a thread."""
    posts = ["First post.", "Second post.", "Third post."]
    assert len(_apply_voice_trim(posts)) == 3
