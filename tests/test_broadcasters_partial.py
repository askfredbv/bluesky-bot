"""Partial-delivery tests for Phase 1 Step 3b.

The old ``@retry_with_backoff`` decorator re-ran the whole broadcaster on
failure — so post 1 and 2 of a 5-post thread would be re-sent if post 3
failed. The rewrite moves retry inside the function with a shared per-thread
budget, so mid-thread failures stop cleanly instead of re-sending.

Tests here verify:
  - A transient error mid-thread eventually exhausts the budget; earlier
    posts were sent exactly once, later posts are *not* attempted, and a
    ``*_partial_delivery`` event is logged.
  - A 429 mid-thread takes the rate-limit path (longer sleeps) and
    honours the shared budget; the sleep helper's ``wait_seconds`` is the
    one surfaced in the log.
  - A successful thread never logs partial-delivery.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src import broadcasters
from src.config import (
    MAX_API_RETRIES,
    RATE_LIMIT_MAX_RETRIES,
)
from src.metrics import BroadcastResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_logger(monkeypatch) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    def _capture(level: str):
        def _call(event, message="", exception=None, **fields):
            events.append({
                "level": level,
                "event": event,
                "error_type": type(exception).__name__ if exception else None,
                **fields,
            })
        return _call

    monkeypatch.setattr(broadcasters.SafeLogger, "info", _capture("info"))
    monkeypatch.setattr(broadcasters.SafeLogger, "warn", _capture("warn"))
    monkeypatch.setattr(broadcasters.SafeLogger, "error", _capture("error"))
    return events


class _FakeResponse:
    """Minimal duck-type for the ``e.response.status_code`` classifier."""
    def __init__(self, status_code: int, headers: Dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}


class _FakeRateLimitError(Exception):
    def __init__(self, retry_after: str | None = None):
        super().__init__("429")
        self.response = _FakeResponse(429, {"retry-after": retry_after} if retry_after else {})


# ---------------------------------------------------------------------------
# Bluesky: mid-thread transient failure → partial delivery, no re-send
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bluesky_transient_failure_mid_thread_stops_and_keeps_earlier_posts(monkeypatch):
    """Post 2 of a 3-post thread fails transiently; budget is shared across
    the thread, so after MAX_API_RETRIES retries the broadcaster gives up
    on post 2. Post 1's URI is preserved. Post 3 is never attempted.
    """
    events = _capture_logger(monkeypatch)

    call_log: List[str] = []
    uri_counter = {"n": 0}

    class DummyClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            call_log.append(text)
            if text == "post-2":
                raise ConnectionError("network dropped")
            uri_counter["n"] += 1

            class FakePost:
                cid = f"cid-{uri_counter['n']}"
                uri = f"at://post/{uri_counter['n']}"
            return FakePost()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    result = await broadcasters.post_to_bluesky(
        DummyClient(), ["post-1", "post-2", "post-3"]
    )

    # post-1 sent once, post-2 attempted (1 initial + MAX_API_RETRIES retries),
    # post-3 never reached.
    assert call_log.count("post-1") == 1, "post-1 must not be re-sent"
    assert call_log.count("post-2") == MAX_API_RETRIES + 1
    assert call_log.count("post-3") == 0

    assert isinstance(result, BroadcastResult)
    assert result.sent_uris == ["at://post/1"]
    assert isinstance(result.error, ConnectionError)

    partials = [e for e in events if e["event"] == "bluesky_partial_delivery"]
    assert len(partials) == 1
    assert partials[0]["posted"] == 1
    assert partials[0]["total"] == 3
    assert partials[0]["reason"] == "error"
    assert partials[0]["error_type"] == "ConnectionError"


# ---------------------------------------------------------------------------
# Bluesky: 429 mid-thread uses rate-limit path and shared budget
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bluesky_rate_limit_exhausts_shared_budget(monkeypatch):
    """Post 2 returns 429 on every attempt. The rate-limit budget is shared
    across the thread, so after RATE_LIMIT_MAX_RETRIES retries the broadcaster
    stops. Sleep values come from the rate-limit helper, not the short
    transient backoff.
    """
    events = _capture_logger(monkeypatch)

    sleep_seconds: List[float] = []

    async def fake_sleep(seconds):
        sleep_seconds.append(seconds)

    monkeypatch.setattr(broadcasters.asyncio, "sleep", fake_sleep)

    call_log: List[str] = []
    uri_counter = {"n": 0}

    class DummyClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            call_log.append(text)
            if text == "post-2":
                raise _FakeRateLimitError(retry_after="65")
            uri_counter["n"] += 1

            class FakePost:
                cid = f"cid-{uri_counter['n']}"
                uri = f"at://post/{uri_counter['n']}"
            return FakePost()

    result = await broadcasters.post_to_bluesky(
        DummyClient(), ["post-1", "post-2", "post-3"]
    )

    assert call_log.count("post-1") == 1
    assert call_log.count("post-2") == RATE_LIMIT_MAX_RETRIES + 1
    assert call_log.count("post-3") == 0

    # Rate-limit sleeps honour the Retry-After header (65s) for every retry
    # slot. Intra-thread jitter between post-1 and post-2 also contributes
    # one small sleep — filter it out by thresholding.
    rate_limit_sleeps = [s for s in sleep_seconds if s >= 60]
    assert len(rate_limit_sleeps) == RATE_LIMIT_MAX_RETRIES
    assert all(s == 65.0 for s in rate_limit_sleeps)

    assert isinstance(result.error, _FakeRateLimitError)
    partials = [e for e in events if e["event"] == "bluesky_partial_delivery"]
    assert len(partials) == 1
    assert partials[0]["posted"] == 1


# ---------------------------------------------------------------------------
# Mastodon: mid-thread transient failure → partial delivery, no re-send
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mastodon_transient_failure_mid_thread_stops_and_keeps_earlier_posts(monkeypatch):
    events = _capture_logger(monkeypatch)

    call_log: List[str] = []
    id_counter = {"n": 0}

    class DummyMastodon:
        def __init__(self, *a, **kw):
            pass

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None):
            call_log.append(status)
            if status == "post-2":
                raise RuntimeError("mastodon blew up")
            id_counter["n"] += 1
            return {"id": id_counter["n"] * 1000}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    result = await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example",
        ["post-1", "post-2", "post-3"],
    )

    assert call_log.count("post-1") == 1
    assert call_log.count("post-2") == MAX_API_RETRIES + 1
    assert call_log.count("post-3") == 0

    assert isinstance(result, BroadcastResult)
    assert result.sent_uris == ["1000"]
    assert isinstance(result.error, RuntimeError)

    partials = [e for e in events if e["event"] == "mastodon_partial_delivery"]
    assert len(partials) == 1
    assert partials[0]["posted"] == 1
    assert partials[0]["total"] == 3
    assert partials[0]["reason"] == "error"


# ---------------------------------------------------------------------------
# Happy paths emit no partial-delivery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bluesky_successful_thread_emits_no_partial_delivery(monkeypatch):
    events = _capture_logger(monkeypatch)

    uri_counter = {"n": 0}

    class DummyClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            uri_counter["n"] += 1

            class FakePost:
                cid = f"cid-{uri_counter['n']}"
                uri = f"at://post/{uri_counter['n']}"
            return FakePost()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)
    result = await broadcasters.post_to_bluesky(DummyClient(), ["a", "b"])

    assert result.error is None
    assert result.sent_uris == ["at://post/1", "at://post/2"]
    assert not any(e["event"] == "bluesky_partial_delivery" for e in events)


@pytest.mark.asyncio
async def test_mastodon_successful_thread_emits_no_partial_delivery(monkeypatch):
    events = _capture_logger(monkeypatch)

    id_counter = {"n": 0}

    class DummyMastodon:
        def __init__(self, *a, **kw):
            pass

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None):
            id_counter["n"] += 1
            return {"id": id_counter["n"] * 1000}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    result = await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example", ["a", "b"]
    )

    assert result.error is None
    assert result.sent_uris == ["1000", "2000"]
    assert not any(e["event"] == "mastodon_partial_delivery" for e in events)
