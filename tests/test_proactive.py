"""Tests for the Phase 4b proactive-reply pipeline.

  - Commit 1: state schema + load/save helpers in ``src/proactive.py``
  - Commit 2: ``generate_proactive_reply`` + voice validator in ``src/agents.py``

Scan / pick-candidate / stage / approve logic lands in subsequent commits.
"""

import pytest

from src import agents, proactive
from src.agents import (
    _is_skip_response,
    _validate_proactive_reply,
    generate_proactive_reply,
)


# ---------------------------------------------------------------------------
# load_pending_replies
# ---------------------------------------------------------------------------

def test_load_returns_empty_skeleton_when_gist_absent(monkeypatch):
    """No state on the Gist → canonical empty skeleton with all three buckets."""
    monkeypatch.setattr(proactive, "_load_gist_state", lambda _f: None)

    state = proactive.load_pending_replies()

    assert state == {"pending": [], "posted": [], "rejected": []}


def test_load_returns_state_when_gist_present(monkeypatch):
    """Gist has full state → returned verbatim."""
    fixture = {
        "pending": [{"id": "abc", "platform": "bluesky", "draft_reply": "hi"}],
        "posted": [{"id": "old", "posted_at": "2026-05-14T10:00:00Z"}],
        "rejected": [{"id": "bad", "rejected_reason": "voice_drift"}],
    }
    monkeypatch.setattr(proactive, "_load_gist_state", lambda _f: fixture)

    state = proactive.load_pending_replies()

    assert state == fixture


def test_load_fills_missing_buckets_with_empty_lists(monkeypatch):
    """Partial state (only 'pending' present) → other buckets default to []."""
    monkeypatch.setattr(
        proactive, "_load_gist_state",
        lambda _f: {"pending": [{"id": "abc"}]},
    )

    state = proactive.load_pending_replies()

    assert state["pending"] == [{"id": "abc"}]
    assert state["posted"] == []
    assert state["rejected"] == []


def test_load_ignores_non_list_bucket_values(monkeypatch):
    """Defensive: malformed bucket (string instead of list) → empty list."""
    monkeypatch.setattr(
        proactive, "_load_gist_state",
        lambda _f: {"pending": "not-a-list", "posted": [], "rejected": []},
    )

    state = proactive.load_pending_replies()

    assert state == {"pending": [], "posted": [], "rejected": []}


def test_load_handles_non_dict_state(monkeypatch):
    """Defensive: Gist returns a list (corrupt) → empty skeleton."""
    monkeypatch.setattr(proactive, "_load_gist_state", lambda _f: ["unexpected"])

    state = proactive.load_pending_replies()

    assert state == {"pending": [], "posted": [], "rejected": []}


def test_load_handles_gist_exception(monkeypatch):
    """Gist read raises → empty skeleton, no crash."""
    def boom(_f):
        raise RuntimeError("Gist unreachable")
    monkeypatch.setattr(proactive, "_load_gist_state", boom)

    state = proactive.load_pending_replies()

    assert state == {"pending": [], "posted": [], "rejected": []}


# ---------------------------------------------------------------------------
# save_pending_replies
# ---------------------------------------------------------------------------

def test_save_persists_state_and_returns_true(monkeypatch):
    """Save delegates to _save_gist_state and returns its result."""
    captured = {}
    def fake_save(filename, data):
        captured["filename"] = filename
        captured["data"] = data
        return True
    monkeypatch.setattr(proactive, "_save_gist_state", fake_save)

    state = {"pending": [{"id": "abc"}], "posted": [], "rejected": []}
    ok = proactive.save_pending_replies(state)

    assert ok is True
    assert captured["filename"] == "pending_replies.json"
    assert captured["data"] == state


def test_save_returns_false_when_gist_save_returns_false(monkeypatch):
    """If the underlying Gist save returns False, propagate it."""
    monkeypatch.setattr(proactive, "_save_gist_state", lambda _f, _d: False)

    ok = proactive.save_pending_replies(proactive._empty_state())

    assert ok is False


def test_save_returns_false_on_exception(monkeypatch):
    """Exception in Gist save → False, no propagation."""
    def boom(_f, _d):
        raise RuntimeError("Gist write failed")
    monkeypatch.setattr(proactive, "_save_gist_state", boom)

    ok = proactive.save_pending_replies(proactive._empty_state())

    assert ok is False


# ---------------------------------------------------------------------------
# new_draft_id
# ---------------------------------------------------------------------------

def test_new_draft_id_returns_unique_strings():
    """UUIDs should not collide across consecutive calls."""
    a = proactive.new_draft_id()
    b = proactive.new_draft_id()

    assert isinstance(a, str)
    assert isinstance(b, str)
    assert a != b
    assert len(a) == 36  # canonical UUID4 string length


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------

def test_empty_state_has_three_buckets():
    """The state contract is exactly these three keys, no others."""
    state = proactive._empty_state()

    assert set(state.keys()) == {"pending", "posted", "rejected"}
    assert all(isinstance(v, list) for v in state.values())


def test_empty_state_returns_fresh_dict():
    """Sanity: callers can mutate without affecting future loads."""
    a = proactive._empty_state()
    b = proactive._empty_state()
    a["pending"].append({"id": "x"})

    assert b["pending"] == []


# ===========================================================================
# Commit 2 — _is_skip_response / _validate_proactive_reply / generate_proactive_reply
# ===========================================================================

# ---------------------------------------------------------------------------
# _is_skip_response
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["SKIP", "skip", " SKIP ", "SKIP.", "skip!", "Skip,", ""])
def test_is_skip_response_detects_skip_literal(text):
    """The SKIP literal — with surrounding whitespace / trailing punctuation — must be detected."""
    assert _is_skip_response(text) is True


@pytest.mark.parametrize("text", [
    "I'll skip this one",                     # narration about skipping ≠ a SKIP signal
    "Skip — nothing to add here",
    "Let me skip that",
    "Actually here's a thought: SKIP that",
    "A real reply that mentions skip in passing.",
])
def test_is_skip_response_does_not_match_skip_inside_longer_text(text):
    """SKIP must be the WHOLE response — not a substring of an explanation."""
    assert _is_skip_response(text) is False


# ---------------------------------------------------------------------------
# _validate_proactive_reply
# ---------------------------------------------------------------------------

def test_validate_accepts_clean_short_reply():
    ok, _ = _validate_proactive_reply(
        "cgroup v1 counts page cache against the memory limit; v2 separates them."
    )
    assert ok is True


def test_validate_rejects_empty():
    ok, reason = _validate_proactive_reply("")
    assert ok is False
    assert "empty" in reason.lower()


def test_validate_rejects_too_short():
    """Under MIN_CHARS the reply can't be doing info-add work."""
    ok, reason = _validate_proactive_reply("Cool point.")
    assert ok is False
    assert "short" in reason.lower()


def test_validate_rejects_over_length():
    """> PROACTIVE_REPLY_MAX_CHARS is rejected even if voice-clean."""
    long_reply = "a" * 250
    ok, reason = _validate_proactive_reply(long_reply)
    assert ok is False
    assert "exceeds" in reason.lower()


def test_validate_rejects_banned_hype():
    """Hype in a reply is more grating than in a broadcast — hard reject."""
    ok, reason = _validate_proactive_reply(
        "This is absolutely game-changing for the field of structured outputs."
    )
    assert ok is False
    assert "hype" in reason.lower()


def test_validate_rejects_banned_opener():
    """Source-summary openers ('Great point about X') burn credibility."""
    ok, reason = _validate_proactive_reply(
        "Tool Tuesday: ripgrep is faster than grep for codebase-wide searches."
    )
    assert ok is False
    assert "opener" in reason.lower()


def test_validate_rejects_reader_bait_ending():
    """Reader-bait questions at the end pull replies into the wrong register."""
    ok, reason = _validate_proactive_reply(
        "cgroup v2 fixes the page-cache accounting bug from v1. What do you think?"
    )
    assert ok is False
    assert "reader-bait" in reason.lower()


def test_validate_rejects_teaser_ending():
    """Broken-promise teasers in a reply context are especially bad — no follow-up exists."""
    ok, reason = _validate_proactive_reply(
        "Anthropic's tool-call enum constraints catch most hallucinations — more soon."
    )
    assert ok is False
    assert "teaser" in reason.lower()


def test_validate_rejects_repetitive_gibberish():
    """Repeated-char runs indicate model malfunction."""
    ok, reason = _validate_proactive_reply("aaaaaaaaaaaaaaaaa is the best approach here.")
    assert ok is False
    assert "gibberish" in reason.lower() or "repetitive" in reason.lower()


# ---------------------------------------------------------------------------
# generate_proactive_reply — orchestrator (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_returns_clean_reply_on_happy_path(monkeypatch):
    """Model produces a voice-clean reply → returned verbatim (post-cleanup)."""
    monkeypatch.setattr(
        agents, "_sync_generate",
        lambda *_a, **_kw:
        "cgroup v1 counts page cache against the memory limit; v2 separates them. "
        "If you're on v1 that 500MB gap is page cache the kernel won't evict.",
    )

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="xeiaso.net",
        parent_text="My pod kept OOMKilling at 1.5GB despite a 2GB limit.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is not None
    assert "cgroup v1" in result
    assert len(result) <= 200


@pytest.mark.asyncio
async def test_generate_returns_none_on_skip_literal(monkeypatch):
    """Model returns SKIP → None (the common case, not an error)."""
    monkeypatch.setattr(agents, "_sync_generate", lambda *_a, **_kw: "SKIP")

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="Excited to announce we're hiring.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is None


@pytest.mark.asyncio
async def test_generate_strips_quote_artifacts(monkeypatch):
    """Model wraps reply in quotes despite the prompt → strip them before validating."""
    monkeypatch.setattr(
        agents, "_sync_generate",
        lambda *_a, **_kw:
        '"pandas.read_html does this in one line but trips on rowspan; '
        'flagging that helps."',
    )

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="Built an HTML→CSV tool.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is not None
    assert not result.startswith('"')
    assert not result.endswith('"')


@pytest.mark.asyncio
async def test_generate_strips_reply_prefix_artifact(monkeypatch):
    """Model echoes the 'Reply:' label from the few-shot block → strip before validating."""
    monkeypatch.setattr(
        agents, "_sync_generate",
        lambda *_a, **_kw:
        "Reply: pandas.read_html does this in one line but trips on rowspan/colspan.",
    )

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="Built an HTML→CSV tool.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is not None
    assert not result.lower().startswith("reply:")


@pytest.mark.asyncio
async def test_generate_returns_none_when_all_attempts_fail_validation(monkeypatch):
    """Every model attempt returns voice-failing output → None, not garbage."""
    monkeypatch.setattr(
        agents, "_sync_generate",
        lambda *_a, **_kw: "This is absolutely game-changing news for the field.",
    )

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="xeiaso.net",
        parent_text="Some technical observation.",
        model_priority=["gemini-2.5-pro", "gemini-2.5-flash"],
    )

    assert result is None


@pytest.mark.asyncio
async def test_generate_returns_none_when_model_chain_exhausts(monkeypatch):
    """API errors on every model → None, no propagation."""
    def boom(*_a, **_kw):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(agents, "_sync_generate", boom)

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="A post.",
        model_priority=["gemini-2.5-pro", "gemini-2.5-flash"],
    )

    assert result is None


@pytest.mark.asyncio
async def test_generate_retries_within_model_on_validation_failure(monkeypatch):
    """First attempt fails validation, second attempt succeeds → return the second."""
    calls = {"count": 0}

    def stub(*_a, **_kw):
        calls["count"] += 1
        if calls["count"] == 1:
            return "This is absolutely amazing news here."  # banned hype → fails
        return "cgroup v1 counts page cache against memory limits; v2 separates them."

    monkeypatch.setattr(agents, "_sync_generate", stub)

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="xeiaso.net",
        parent_text="OOMKill at 1.5GB.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is not None
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_generate_falls_through_to_next_model_on_api_error(monkeypatch):
    """First model raises (API error), second model succeeds → return second model's output."""
    calls = {"models_seen": []}

    def stub(_api_key, _instr, _task, model):
        calls["models_seen"].append(model)
        if model == "gemini-2.5-pro":
            raise RuntimeError("quota exhausted")
        return "Postfix releases since 1998 have shipped with TLS off by default for compatibility — the change is opt-in."

    monkeypatch.setattr(agents, "_sync_generate", stub)

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="Notes on email server config.",
        model_priority=["gemini-2.5-pro", "gemini-2.5-flash"],
    )

    assert result is not None
    assert calls["models_seen"][0] == "gemini-2.5-pro"
    assert "gemini-2.5-flash" in calls["models_seen"]
