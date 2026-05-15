"""Tests for Bluesky session string caching (src/bluesky_session.py).

The cache is driven by atproto's ``on_session_change`` hook:
  - ``SessionEvent.CREATE``  fires after a successful password login
  - ``SessionEvent.REFRESH`` fires when the SDK auto-rotates the JWT pair mid-run
  - ``SessionEvent.IMPORT``  fires when a cached session string is loaded

We persist on CREATE and REFRESH (per the SDK's documented advice) and skip
IMPORT (rewriting the same value would just churn the Gist).

Scenarios covered:
  1. Cached session valid              → session_string login, IMPORT skipped, no save
  2. Cached session stale              → falls back to password login, CREATE event saves
  3. No cached session                 → password login, CREATE event saves
  4. Gist read raises                  → treated as no cache, password login still saves
  5. REFRESH mid-run                   → save fires on the new session (the v4.20.1 bug fix)
  6. Persist failure on session-change → logged and swallowed, run does not crash
"""

import pytest
from atproto import SessionEvent
from atproto_client.client.session import Session
from src import bluesky_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(export_value: str) -> Session:
    """Build a Session whose ``.export()`` returns ``export_value``.

    We can't easily construct a real Session with arbitrary JWTs, so we stub
    ``.export()`` directly. The callback only ever calls ``.export()`` on the
    Session it receives.
    """
    s = Session.__new__(Session)
    s.export = lambda: export_value  # type: ignore[method-assign]
    return s


class _FakeClient:
    """AsyncClient stand-in that records logins and fires SessionEvent callbacks.

    Mirrors the real SDK: the session-change callback is invoked with
    SessionEvent.IMPORT after a successful session-string login, and with
    SessionEvent.CREATE after a successful password login.
    """

    def __init__(self, *, session_raises: bool = False, export_value: str = "new-session"):
        self._session_raises = session_raises
        self._export_value = export_value
        self.login_calls: list[dict] = []
        self._callback = None

    def on_session_change(self, callback):
        self._callback = callback
        return callback

    async def _fire(self, event: SessionEvent, export_value: str) -> None:
        if self._callback is not None:
            await self._callback(event, _make_session(export_value))

    async def login(self, login=None, password=None, session_string=None, **_):
        call: dict = {}
        if session_string is not None:
            call["session_string"] = session_string
        if login is not None:
            call["username"] = login
        if password is not None:
            call["password"] = password
        self.login_calls.append(call)

        if session_string is not None:
            if self._session_raises:
                raise RuntimeError("session expired")
            await self._fire(SessionEvent.IMPORT, session_string)
        else:
            await self._fire(SessionEvent.CREATE, self._export_value)

    # Test-only: simulate atproto rotating the JWT mid-run.
    async def simulate_refresh(self, new_export: str) -> None:
        await self._fire(SessionEvent.REFRESH, new_export)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_cached_session_reused(monkeypatch):
    """Cache hit → session-string login, IMPORT fires but we don't persist."""
    saved = {}
    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: "cached-token")
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"value": s}))

    client = _FakeClient()
    await bluesky_session.load_or_login(client, "user", "pass")

    assert client.login_calls == [{"session_string": "cached-token"}]
    assert "value" not in saved  # IMPORT is not persisted


@pytest.mark.asyncio
async def test_stale_cached_session_falls_back_to_password_login(monkeypatch):
    """Cache hit but stale → password login → CREATE event persists new session."""
    saved = {}
    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: "stale-token")
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"value": s}))

    client = _FakeClient(session_raises=True, export_value="new-session-abc")
    await bluesky_session.load_or_login(client, "user", "pass")

    assert len(client.login_calls) == 2
    assert "session_string" in client.login_calls[0]
    assert client.login_calls[1] == {"username": "user", "password": "pass"}
    assert saved["value"] == "new-session-abc"


@pytest.mark.asyncio
async def test_no_cached_session_uses_password_login_and_saves(monkeypatch):
    """No cache → password login → CREATE event persists session."""
    saved = {}
    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: None)
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"value": s}))

    client = _FakeClient(export_value="brand-new-session")
    await bluesky_session.load_or_login(client, "user", "pass")

    assert client.login_calls == [{"username": "user", "password": "pass"}]
    assert saved["value"] == "brand-new-session"


@pytest.mark.asyncio
async def test_gist_read_failure_treated_as_no_cache(monkeypatch):
    """Gist read raises → treated as no cache → password login still persists."""
    saved = {}
    monkeypatch.setattr(
        bluesky_session, "_load_gist_state",
        lambda f: (_ for _ in ()).throw(RuntimeError("Gist unreachable")),
    )
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saved.update({"value": s}))

    client = _FakeClient(export_value="fresh-session")
    await bluesky_session.load_or_login(client, "user", "pass")

    assert client.login_calls == [{"username": "user", "password": "pass"}]
    assert saved["value"] == "fresh-session"


@pytest.mark.asyncio
async def test_mid_run_refresh_persists_new_session(monkeypatch):
    """REFRESH event mid-run → save fires with the rotated session.

    This is the v4.20.1 bug fix: without the on_session_change hook on
    REFRESH, the cache went stale during the run because atproto rotates
    the JWT pair internally and invalidates the previous refresh_jwt
    server-side. Next run loaded a revoked refresh token.
    """
    saves: list[str] = []
    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: None)
    monkeypatch.setattr(bluesky_session, "_save_cached_session", lambda s: saves.append(s))

    client = _FakeClient(export_value="initial-session")
    await bluesky_session.load_or_login(client, "user", "pass")

    # Simulate atproto rotating the session mid-run (e.g. after a long API call)
    await client.simulate_refresh("rotated-session-after-jwt-refresh")

    assert saves == ["initial-session", "rotated-session-after-jwt-refresh"]


@pytest.mark.asyncio
async def test_persist_failure_on_session_change_is_non_fatal(monkeypatch):
    """If _save_cached_session raises, the run continues — the failure is logged."""
    monkeypatch.setattr(bluesky_session, "_load_cached_session", lambda: None)

    def _boom(_s):
        raise RuntimeError("Gist write failed")
    monkeypatch.setattr(bluesky_session, "_save_cached_session", _boom)

    client = _FakeClient(export_value="some-session")
    # Should not raise — the persist failure is caught inside the callback
    await bluesky_session.load_or_login(client, "user", "pass")

    assert client.login_calls == [{"username": "user", "password": "pass"}]
