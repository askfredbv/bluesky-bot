"""Tests for the Phase 4b commit-5 entry point — ``scripts/run_proactive_approve``.

Verifies the wiring around src/proactive's approve/reject helpers:
input validation, the reject path skipping Bluesky login entirely,
the approve path requiring creds, and the script's exit-code contract
(0 only on a clean success; 1 on any failure — strict because the user
needs a red X in the Actions UI when something didn't go through).
"""

from datetime import datetime, timezone

import pytest

from scripts import run_proactive_approve
from src import proactive


_NOW = datetime(2026, 5, 21, 18, 0, tzinfo=timezone.utc)


def _draft(draft_id="abc"):
    return {
        "id": draft_id,
        "platform": "bluesky",
        "parent_author": "simonwillison.net",
        "parent_post_uri": "at://parent/x",
        "parent_text": "A parent post.",
        "draft_reply": "A draft reply.",
        "generated_at": _NOW.isoformat(),
        "expires_at": _NOW.isoformat(),
    }


def _stub_creds(monkeypatch):
    monkeypatch.setenv("BLUESKY_USERNAME", "bot")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pass")


@pytest.mark.asyncio
async def test_bad_action_returns_1(monkeypatch):
    """ACTION must be 'approve' or 'reject' — anything else exits 1."""
    monkeypatch.setenv("ACTION", "delete")  # nonsense
    monkeypatch.setenv("DRAFT_ID", "first")

    rc = await run_proactive_approve._run()

    assert rc == 1


@pytest.mark.asyncio
async def test_reject_path_skips_login_and_returns_0(monkeypatch):
    """Reject doesn't need Bluesky creds — just state mutation + Gist write."""
    monkeypatch.setenv("ACTION", "reject")
    monkeypatch.setenv("DRAFT_ID", "abc")
    monkeypatch.setenv("REJECT_REASON", "off voice")
    # Deliberately no Bluesky creds — reject must not need them.
    monkeypatch.delenv("BLUESKY_USERNAME", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)

    state = {"pending": [_draft(draft_id="abc")], "posted": [], "rejected": []}
    monkeypatch.setattr(proactive, "load_pending_replies", lambda: (state, True))
    save_calls = []
    monkeypatch.setattr(proactive, "save_pending_replies",
                        lambda s: save_calls.append(s) or True)

    rc = await run_proactive_approve._run()

    assert rc == 0
    assert state["pending"] == []
    assert state["rejected"][0]["rejected_reason"] == "off voice"
    assert len(save_calls) == 1


@pytest.mark.asyncio
async def test_reject_unknown_id_returns_1(monkeypatch):
    """Reject with no matching draft → save still happens, but exit 1."""
    monkeypatch.setenv("ACTION", "reject")
    monkeypatch.setenv("DRAFT_ID", "not-there")

    state = {"pending": [_draft(draft_id="abc")], "posted": [], "rejected": []}
    monkeypatch.setattr(proactive, "load_pending_replies", lambda: (state, True))
    monkeypatch.setattr(proactive, "save_pending_replies", lambda _s: True)

    rc = await run_proactive_approve._run()

    assert rc == 1


@pytest.mark.asyncio
async def test_approve_missing_creds_returns_1(monkeypatch):
    """Approve path needs Bluesky creds — missing → exit 1 before any state I/O."""
    monkeypatch.setenv("ACTION", "approve")
    monkeypatch.setenv("DRAFT_ID", "first")
    monkeypatch.delenv("BLUESKY_USERNAME", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)

    monkeypatch.setattr(proactive, "load_pending_replies", lambda: (proactive._empty_state(), True))

    rc = await run_proactive_approve._run()

    assert rc == 1


@pytest.mark.asyncio
async def test_approve_happy_path_returns_0(monkeypatch):
    """Approve with creds, working atproto + login + approve_draft → exit 0."""
    _stub_creds(monkeypatch)
    monkeypatch.setenv("ACTION", "approve")
    monkeypatch.setenv("DRAFT_ID", "abc")

    state = {"pending": [_draft(draft_id="abc")], "posted": [], "rejected": []}
    monkeypatch.setattr(proactive, "load_pending_replies", lambda: (state, True))

    # Stub the atproto import + login
    import sys
    import types

    class _FakeAsyncClient:
        def __init__(self, *_a, **_kw):
            pass

    monkeypatch.setitem(
        sys.modules,
        "atproto",
        types.SimpleNamespace(AsyncClient=_FakeAsyncClient),
    )

    async def _noop_login(*_a, **_kw):
        return None
    monkeypatch.setattr(run_proactive_approve, "load_or_login", _noop_login)

    # Stub approve_draft to return a successful posted entry
    async def _fake_approve(draft_id, client, state, now):
        draft = next(d for d in state["pending"] if d["id"] == draft_id)
        state["pending"].remove(draft)
        posted = {**draft, "posted_at": now.isoformat(), "posted_uri": "at://posted"}
        state["posted"].append(posted)
        return posted
    monkeypatch.setattr(proactive, "approve_draft", _fake_approve)

    save_calls = []
    monkeypatch.setattr(proactive, "save_pending_replies",
                        lambda s: save_calls.append(s) or True)

    rc = await run_proactive_approve._run()

    assert rc == 0
    assert state["pending"] == []
    assert len(state["posted"]) == 1
    assert len(save_calls) == 1


@pytest.mark.asyncio
async def test_state_save_failure_returns_1(monkeypatch):
    """Even when the action succeeded, a Gist save failure exits 1 — inconsistency."""
    monkeypatch.setenv("ACTION", "reject")
    monkeypatch.setenv("DRAFT_ID", "abc")

    state = {"pending": [_draft(draft_id="abc")], "posted": [], "rejected": []}
    monkeypatch.setattr(proactive, "load_pending_replies", lambda: (state, True))
    monkeypatch.setattr(proactive, "save_pending_replies", lambda _s: False)

    rc = await run_proactive_approve._run()

    assert rc == 1


@pytest.mark.asyncio
async def test_untrusted_load_aborts_without_saving(monkeypatch):
    """Gist read failed (trusted=False) → exit 1, NO save.

    Regression guard for the Codex 2026-06-12 finding: a failed read returns
    an empty state; saving it would clobber real pending/posted/rejected.
    The reject path runs before any creds check, so this also proves the
    abort happens regardless of action.
    """
    monkeypatch.setenv("ACTION", "reject")
    monkeypatch.setenv("DRAFT_ID", "abc")

    monkeypatch.setattr(proactive, "load_pending_replies",
                        lambda: (proactive._empty_state(), False))
    save_calls = []
    monkeypatch.setattr(proactive, "save_pending_replies",
                        lambda s: save_calls.append(s) or True)

    rc = await run_proactive_approve._run()

    assert rc == 1
    assert save_calls == []  # never saved → real state preserved
