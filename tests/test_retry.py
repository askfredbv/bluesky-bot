import asyncio
import pytest

from src import utils
from src.config import RATE_LIMIT_BASE_WAIT_SECONDS, RATE_LIMIT_MAX_RETRIES, MAX_API_RETRIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sleep_capture(monkeypatch):
    """Patch asyncio.sleep on utils and return a list that records every call."""
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(utils.asyncio, "sleep", fake_sleep)
    return calls


def _make_429_error(retry_after: str = None):
    class FakeResponse:
        status_code = 429
        headers = {"retry-after": retry_after} if retry_after else {}

    class Fake429Error(Exception):
        response = FakeResponse()

    return Fake429Error


# ---------------------------------------------------------------------------
# 429-specific path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_waits_at_least_base_seconds_on_429(monkeypatch):
    """A 429 response triggers the rate-limit path with waits >= RATE_LIMIT_BASE_WAIT_SECONDS."""
    sleep_calls = _make_sleep_capture(monkeypatch)
    Fake429Error = _make_429_error()

    @utils.retry_with_backoff
    async def always_429():
        raise Fake429Error()

    with pytest.raises(Fake429Error):
        await always_429()

    assert len(sleep_calls) == RATE_LIMIT_MAX_RETRIES
    assert all(s >= RATE_LIMIT_BASE_WAIT_SECONDS for s in sleep_calls)


@pytest.mark.asyncio
async def test_retry_429_uses_retry_after_header_value(monkeypatch):
    """When a Retry-After header is present its value is used as the wait time."""
    sleep_calls = _make_sleep_capture(monkeypatch)
    Fake429Error = _make_429_error(retry_after="90")

    @utils.retry_with_backoff
    async def always_429_with_header():
        raise Fake429Error()

    with pytest.raises(Fake429Error):
        await always_429_with_header()

    assert sleep_calls[0] == 90.0


@pytest.mark.asyncio
async def test_retry_429_exhausts_separate_budget(monkeypatch):
    """The 429 path uses its own retry counter — RATE_LIMIT_MAX_RETRIES attempts."""
    sleep_calls = _make_sleep_capture(monkeypatch)
    call_count = [0]
    Fake429Error = _make_429_error()

    @utils.retry_with_backoff
    async def always_429():
        call_count[0] += 1
        raise Fake429Error()

    with pytest.raises(Fake429Error):
        await always_429()

    # Initial attempt + RATE_LIMIT_MAX_RETRIES retries
    assert call_count[0] == RATE_LIMIT_MAX_RETRIES + 1
    assert len(sleep_calls) == RATE_LIMIT_MAX_RETRIES


# ---------------------------------------------------------------------------
# Non-429 path (unchanged behaviour)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_non_429_uses_short_exponential_backoff(monkeypatch):
    """Non-429 errors use the short exponential backoff — well under 60 seconds."""
    sleep_calls = _make_sleep_capture(monkeypatch)

    @utils.retry_with_backoff
    async def always_network_error():
        raise ConnectionError("timeout")

    with pytest.raises(ConnectionError):
        await always_network_error()

    assert len(sleep_calls) == MAX_API_RETRIES - 1
    assert all(s < RATE_LIMIT_BASE_WAIT_SECONDS for s in sleep_calls)


@pytest.mark.asyncio
async def test_retry_non_429_exhausts_after_max_api_retries(monkeypatch):
    """Non-429 errors exhaust MAX_API_RETRIES and then raise."""
    _make_sleep_capture(monkeypatch)
    call_count = [0]

    @utils.retry_with_backoff
    async def always_fails():
        call_count[0] += 1
        raise RuntimeError("generic failure")

    with pytest.raises(RuntimeError):
        await always_fails()

    assert call_count[0] == MAX_API_RETRIES
