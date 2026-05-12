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
from atproto import AsyncClient
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
      1. Load cached session string from Gist.
      2. If present, attempt session-string login — fast and credential-free.
      3. If absent or stale (any exception), fall through to password login.
      4. After a successful password login, export and cache the new session.

    The caller does not need to distinguish between the two paths — the
    client is authenticated either way on return, or an exception propagates
    (letting the caller decide whether to skip Bluesky for this run).
    """
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
            # v4.19 (2026-05-12): error_msg added per retro discipline. The
            # session_stale event fires on basically every natural run with
            # error_type=BadRequestError — the cache is decorative right now.
            # Without the message body we cannot tell whether atproto is
            # rejecting the access JWT (TTL ~2h), the refresh JWT, the session
            # bundle format, or something else. Next run's error_msg surfaces it.
            SafeLogger.info(
                "bluesky_session_stale",
                "Cached Bluesky session invalid; falling back to password login",
                error_type=type(e).__name__,
                error_msg=str(e)[:200],
            )

    # Fresh password login
    await client.login(username, password)

    # Cache the new session for the next run
    try:
        session_string = client.export_session_string()
        _save_cached_session(session_string)
        SafeLogger.info(
            "bluesky_session_cached",
            "New Bluesky session cached for reuse",
        )
    except Exception as e:
        # Non-fatal — the run continues without caching
        SafeLogger.warn(
            "bluesky_session_export_failed",
            "Could not export Bluesky session string; next run will re-authenticate",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
