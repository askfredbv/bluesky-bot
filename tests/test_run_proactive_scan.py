"""Tests for the Phase 4b commit-4 entry point — ``scripts/run_proactive_scan``.

The script is mostly orchestration: load state → scan → pick → generate →
stage → save. Individual pieces are well-tested in test_proactive.py; here
we verify the wiring (which branches call save, which return early, what
happens when creds are missing).
"""

from datetime import datetime, timezone

import pytest

from scripts import run_proactive_scan
from src import proactive


_NOW = datetime(2026, 5, 21, 18, 0, tzinfo=timezone.utc)


def _candidate(
    *,
    handle="simonwillison.net",
    text="A clean post worth replying to.",
    engagement=5,
    uri="at://did:plc:abc/app.bsky.feed.post/xyz",
):
    return {
        "platform": "bluesky",
        "parent_author": handle,
        "parent_post_uri": uri,
        "parent_text": text,
        "engagement": engagement,
        "indexed_at": _NOW.isoformat(),
    }


def _stub_creds(monkeypatch):
    """Set the three required env vars so the early-exit path is bypassed."""
    monkeypatch.setenv("BLUESKY_USERNAME", "bot")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pass")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")


def _stub_atproto_and_session(monkeypatch):
    """Patch the atproto client + load_or_login so no real HTTP happens."""
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
    monkeypatch.setattr(run_proactive_scan, "load_or_login", _noop_login)


@pytest.mark.asyncio
async def test_returns_zero_when_creds_missing(monkeypatch):
    """No creds → log + early-exit 0. The next scheduled run retries."""
    for key in ("BLUESKY_USERNAME", "BLUESKY_APP_PASSWORD", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    rc = await run_proactive_scan._run()

    assert rc == 0


@pytest.mark.asyncio
async def test_no_candidates_does_not_save_when_state_unchanged(monkeypatch):
    """Empty scan + no expiry mutation → exit 0, do NOT write to Gist."""
    _stub_creds(monkeypatch)
    _stub_atproto_and_session(monkeypatch)

    monkeypatch.setattr(proactive, "load_pending_replies", lambda: proactive._empty_state())

    async def _empty_scan(*_a, **_kw):
        return []
    monkeypatch.setattr(proactive, "scan_watchlist", _empty_scan)

    save_calls = []
    monkeypatch.setattr(proactive, "save_pending_replies", lambda s: save_calls.append(s) or True)

    rc = await run_proactive_scan._run()

    assert rc == 0
    assert save_calls == []  # nothing changed → no Gist write


@pytest.mark.asyncio
async def test_no_candidates_does_save_when_expiry_changed_state(monkeypatch):
    """Empty scan but scan_watchlist expired a draft → save once."""
    _stub_creds(monkeypatch)
    _stub_atproto_and_session(monkeypatch)

    initial = {
        "pending": [{"id": "old", "parent_author": "x", "generated_at": "old-ts"}],
        "posted": [], "rejected": [],
    }
    monkeypatch.setattr(proactive, "load_pending_replies", lambda: initial)

    async def _expire_then_no_candidates(client, watchlist, state, now):
        # Simulate expire_old_drafts having moved the pending entry to rejected
        moved = state["pending"].pop(0)
        state["rejected"].append({**moved, "rejected_reason": "expired"})
        return []
    monkeypatch.setattr(proactive, "scan_watchlist", _expire_then_no_candidates)

    save_calls = []
    monkeypatch.setattr(proactive, "save_pending_replies", lambda s: save_calls.append(s) or True)

    rc = await run_proactive_scan._run()

    assert rc == 0
    assert len(save_calls) == 1
    assert save_calls[0]["pending"] == []
    assert len(save_calls[0]["rejected"]) == 1


@pytest.mark.asyncio
async def test_model_returns_skip_does_not_stage(monkeypatch):
    """Candidates exist → pick → model SKIPs → no draft staged, no Gist write."""
    _stub_creds(monkeypatch)
    _stub_atproto_and_session(monkeypatch)

    monkeypatch.setattr(proactive, "load_pending_replies", lambda: proactive._empty_state())

    async def _one_candidate(*_a, **_kw):
        return [_candidate()]
    monkeypatch.setattr(proactive, "scan_watchlist", _one_candidate)

    async def _model_skips(**_kw):
        return None
    monkeypatch.setattr(run_proactive_scan, "generate_proactive_reply", _model_skips)

    stage_calls = []
    monkeypatch.setattr(proactive, "stage_draft_reply",
                        lambda *a, **kw: stage_calls.append(a) or {"id": "x"})
    save_calls = []
    monkeypatch.setattr(proactive, "save_pending_replies",
                        lambda s: save_calls.append(s) or True)

    rc = await run_proactive_scan._run()

    assert rc == 0
    assert stage_calls == []
    assert save_calls == []


@pytest.mark.asyncio
async def test_happy_path_stages_and_saves(monkeypatch):
    """Candidates → pick → model returns reply → stage → save."""
    _stub_creds(monkeypatch)
    _stub_atproto_and_session(monkeypatch)

    state = proactive._empty_state()
    monkeypatch.setattr(proactive, "load_pending_replies", lambda: state)

    async def _two_candidates(*_a, **_kw):
        return [
            _candidate(engagement=3, text="lower engagement"),
            _candidate(engagement=8, text="higher engagement"),
        ]
    monkeypatch.setattr(proactive, "scan_watchlist", _two_candidates)

    async def _model_returns_reply(**kw):
        # Confirm the picker fed the higher-engagement candidate to the model
        assert kw["parent_text"] == "higher engagement"
        return "cgroup v1 counts page cache against the memory limit; v2 separates them."
    monkeypatch.setattr(run_proactive_scan, "generate_proactive_reply", _model_returns_reply)

    save_calls = []
    monkeypatch.setattr(proactive, "save_pending_replies",
                        lambda s: save_calls.append(s) or True)

    rc = await run_proactive_scan._run()

    assert rc == 0
    assert len(state["pending"]) == 1
    assert state["pending"][0]["parent_text"] == "higher engagement"
    assert state["pending"][0]["draft_reply"].startswith("cgroup v1")
    assert len(save_calls) == 1
