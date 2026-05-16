"""Tests for the Phase 4b proactive-reply state I/O (src/proactive.py).

First commit scope: state schema + load/save helpers only. Scan / generate /
approve logic lands in subsequent commits.
"""

import pytest

from src import proactive


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
