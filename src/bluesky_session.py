"""Bluesky session string persistence via the Gist state store.

Caching the session string avoids a full credential login on every run,
which reduces 429 exposure and shaves a round-trip off startup time.

The session string is stored in the same private Gist that holds
seen_articles.json and replied_to.json — no extra secrets required.

On a cache hit the session is tried first; any failure (expired, revoked,
API change) triggers a transparent fall-through to a fresh password login
and the new session is written back to the Gist.
"""

from typing import Optional
from atproto import AsyncClient, SessionEvent
from atproto_client.client.session import Session
from src.logger import SafeLogger


# Import the private Gist helpers directly — the session file is a plain
# string value wrapped in a single-key dict to stay consistent with the
# JSON structure the other state files use.
from src.utils import _load_gist_state, _save_gist_state

_SESSION_FILENAME = "bluesky_session.json"


def _load_cached_session() -> Optional[str]:
    """Return the cached session string, or None if absent / unreadable."""
    try:
        data = _load_gist_state(_SESSION_FILENAME)
        if isinstance(data, dict):
            value = data.get("session_string")
            return value if isinstance(value, str) and value else None
    except Exception:
        pass
    return None


def _save_cached_session(session_string: str) -> None:
    """Persist the session string to the Gist; log a warning on failure."""
    try:
        _save_gist_state(_SESSION_FILENAME, {"session_string": session_string})
    except Exception as e:
        SafeLogger.warn(
            "bluesky_session_save_failed",
            "Failed to persist Bluesky session string to Gist",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )


async def load_or_login(
    client: AsyncClient, username: str, password: str
) -> None:
    """Authenticate the client, preferring a cached session over a fresh login.

    Flow:
      1. Register an ``on_session_change`` callback so the cache stays current
         across both initial CREATE (after password login) and any mid-run
         REFRESH (when atproto auto-rotates the JWT pair).
      2. Load cached session string from Gist.
      3. If present, attempt session-string login — fast and credential-free.
      4. If absent or stale (any exception), fall through to password login.

    The caller does not need to distinguish between the two paths — the
    client is authenticated either way on return, or an exception propagates
    (letting the caller decide whether to skip Bluesky for this run).

    v4.20.1 (2026-05-15) — fixes the ``bluesky_session_stale`` revocation
    loop. Root cause: atproto auto-rotates the JWT pair during the run when
    the access JWT nears expiry; each rotation invalidates the previous
    refresh_jwt server-side. Pre-v4.20.1 we only exported once after
    password login, so the cached pair went stale *during* the run.
    Next run loaded the now-revoked refresh token → "Token has been revoked".
    Fix: persist on CREATE *and* REFRESH via the SDK's documented hook.
    """

    @client.on_session_change
    async def _persist_session(event: SessionEvent, session: Session) -> None:
        # Persist on CREATE (after password login) and REFRESH (mid-run
        # JWT rotation). Skip IMPORT — that fires when we hand the SDK
        # the cached string, and rewriting the same value would just
        # noise the Gist patch history.
        if event in (SessionEvent.CREATE, SessionEvent.REFRESH):
            try:
                _save_cached_session(session.export())
                SafeLogger.info(
                    "bluesky_session_persisted",
                    "Bluesky session persisted to Gist",
                    session_event=event.value,
                )
            except Exception as e:
                # Non-fatal — the run continues; next session-change retries
                SafeLogger.warn(
                    "bluesky_session_persist_failed",
                    "Could not persist Bluesky session on session-change event",
                    session_event=event.value,
                    error_type=type(e).__name__,
                    error_msg=str(e)[:200],
                )

    cached = _load_cached_session()

    if cached:
        try:
            await client.login(session_string=cached)
            SafeLogger.info(
                "bluesky_session_reused",
                "Reused cached Bluesky session — skipped password login",
            )
            return
        except Exception as e:
            SafeLogger.info(
                "bluesky_session_stale",
                "Cached Bluesky session invalid; falling back to password login",
                error_type=type(e).__name__,
                error_msg=str(e)[:200],
            )

    # Fresh password login — the CREATE event from the callback above
    # will persist the new session.
    await client.login(username, password)
