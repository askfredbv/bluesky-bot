"""Tests for Bluesky session string caching (src/bluesky_session.py).

Four scenarios:
  1. Cached session valid      → one login call (session_string), no password, session not re-saved
  2. Cached session stale      → session login raises, falls back to password login, new session saved
  3. No cached session         → password login directly, new session saved
  4. Gist read raises          → treated as no cache, password login proceeds without crashing
"""

import pytest
from src import bluesky_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeClient:
    """Minimal AsyncClient stand-in that records how it was authenticated."""

    def __init__(self, *, session_raises=False, export_string="fake-session-xyz"):
        self._session_raises = session_raises
        self._export_string = export_string
        self.login_calls = []   # list of kwarg dicts passed to login()

    async def login(self, username=None, password=None, session_string=None):
        call = {}
        if session_string is not None:
            call["session_string"] = session_string
        if username is not None:
            call["username"] = username
        if password is not None:
            call["password"] = password
        self.login_calls.append(call)
        if session_string is not None and self._session_raises:
            raise RuntimeError("session expired")

    def export_session_string(self):
        return self._export_string


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_cached_session_reused(monkeypatch):
    """When a cached session exists and works, only one session-string login happens."""
    saved = {}

    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: "cached-token")
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"called": True}))

    client = _FakeClient()
    await bluesky_session.load_or_login(client, "user", "pass")

    assert len(client.login_calls) == 1
    assert client.login_calls[0] == {"session_string": "cached-token"}
    assert "called" not in saved   # session not rewritten on cache hit


@pytest.mark.asyncio
async def test_stale_cached_session_falls_back_to_password_login(monkeypatch):
    """When cached session login raises, falls back to password login and saves new session."""
    saved = {}

    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: "stale-token")
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"session": s}))

    client = _FakeClient(session_raises=True, export_string="new-session-abc")
    await bluesky_session.load_or_login(client, "user", "pass")

    assert len(client.login_calls) == 2
    assert "session_string" in client.login_calls[0]   # first attempt: session
    assert client.login_calls[1] == {"username": "user", "password": "pass"}  # fallback
    assert saved["session"] == "new-session-abc"


@pytest.mark.asyncio
async def test_no_cached_session_uses_password_login_and_saves(monkeypatch):
    """When no cached session exists, password login is used and new session is saved."""
    saved = {}

    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: None)
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"session": s}))

    client = _FakeClient(export_string="brand-new-session")
    await bluesky_session.load_or_login(client, "user", "pass")

    assert len(client.login_calls) == 1
    assert client.login_calls[0] == {"username": "user", "password": "pass"}
    assert saved["session"] == "brand-new-session"


@pytest.mark.asyncio
async def test_gist_read_failure_treated_as_no_cache(monkeypatch):
    """If the underlying Gist read raises, _load_cached_session returns None — no crash."""
    saved = {}

    # Patch the inner Gist call to simulate an unreachable Gist
    monkeypatch.setattr(bluesky_session, "_load_gist_state", lambda f: (_ for _ in ()).throw(RuntimeError("Gist unreachable")))
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"session": s}))

    client = _FakeClient(export_string="fresh-session")
    await bluesky_session.load_or_login(client, "user", "pass")

    assert len(client.login_calls) == 1
    assert client.login_calls[0] == {"username": "user", "password": "pass"}
    assert saved["session"] == "fresh-session"


@pytest.mark.asyncio
async def test_export_failure_is_non_fatal(monkeypatch):
    """If export_session_string raises, the run continues without caching."""
    saved = {}

    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: None)
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"session": s}))

    class _BrokenExportClient(_FakeClient):
        def export_session_string(self):
            raise RuntimeError("export failed")

    client = _BrokenExportClient()
    # Should not raise — export failure is logged and swallowed
    await bluesky_session.load_or_login(client, "user", "pass")

    assert len(client.login_calls) == 1
    assert "session" not in saved   # nothing saved, but no crash
