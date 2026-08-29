from datetime import datetime, timezone

import pytest

from src import retry, utils
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

    # 1 initial attempt + MAX_API_RETRIES retries = MAX_API_RETRIES + 1 calls,
    # with a sleep before each retry → MAX_API_RETRIES sleeps total.
    assert len(sleep_calls) == MAX_API_RETRIES
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

    # 1 initial attempt + MAX_API_RETRIES retries before exhaustion raises.
    assert call_count[0] == MAX_API_RETRIES + 1


# ---------------------------------------------------------------------------
# classify_retry — boundary cases
# ---------------------------------------------------------------------------

def _exc_with_status(status):
    class FakeResponse:
        status_code = status
        headers = {}

    class FakeError(Exception):
        response = FakeResponse()

    return FakeError()


def test_classify_retry_returns_rate_limit_for_429():
    assert utils.classify_retry(_exc_with_status(429)) == "rate_limit"


def test_classify_retry_returns_transient_for_500():
    assert utils.classify_retry(_exc_with_status(500)) == "transient"


def test_classify_retry_returns_transient_when_no_response_attr():
    assert utils.classify_retry(ConnectionError("timeout")) == "transient"


def test_classify_retry_returns_transient_when_status_is_none():
    class FakeResponse:
        status_code = None
        headers = {}

    class FakeError(Exception):
        response = FakeResponse()

    assert utils.classify_retry(FakeError()) == "transient"


# ---------------------------------------------------------------------------
# Header normalisation
# ---------------------------------------------------------------------------

def test_parse_retry_after_accepts_seconds():
    assert utils._parse_retry_after_header("90") == 90.0


def test_parse_retry_after_accepts_http_date(monkeypatch):
    # HTTP-date 60 seconds in the future from a pinned "now"
    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now

    monkeypatch.setattr(retry, "datetime", FakeDatetime)
    result = utils._parse_retry_after_header("Wed, 22 Apr 2026 12:01:00 GMT")
    assert result is not None
    assert 59.0 <= result <= 61.0


def test_parse_retry_after_returns_none_for_garbage():
    assert utils._parse_retry_after_header("not-a-thing") is None


def test_parse_retry_after_clamps_past_date_to_zero(monkeypatch):
    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now

    monkeypatch.setattr(retry, "datetime", FakeDatetime)
    assert utils._parse_retry_after_header("Wed, 22 Apr 2026 11:59:00 GMT") == 0.0


def test_parse_ratelimit_reset_accepts_unix_timestamp(monkeypatch):
    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now
        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(retry, "datetime", FakeDatetime)
    reset = pinned_now.timestamp() + 120
    assert utils._parse_ratelimit_reset_header(str(reset)) == pytest.approx(120.0, abs=1.0)


def test_parse_ratelimit_reset_accepts_iso8601(monkeypatch):
    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now
        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(retry, "datetime", FakeDatetime)
    result = utils._parse_ratelimit_reset_header("2026-04-22T12:02:00+00:00")
    assert result == pytest.approx(120.0, abs=1.0)


def test_parse_ratelimit_reset_returns_none_for_garbage():
    assert utils._parse_ratelimit_reset_header("garbage") is None


def test_extract_rate_limit_wait_prefers_retry_after():
    class FakeResponse:
        headers = {"retry-after": "45", "x-ratelimit-reset": "999999"}

    assert utils._extract_rate_limit_wait(FakeResponse()) == 45.0


def test_extract_rate_limit_wait_falls_back_to_x_ratelimit_reset(monkeypatch):
    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now
        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(retry, "datetime", FakeDatetime)

    class FakeResponse:
        headers = {"x-ratelimit-reset": str(pinned_now.timestamp() + 30)}

    assert utils._extract_rate_limit_wait(FakeResponse()) == pytest.approx(30.0, abs=1.0)


def test_extract_rate_limit_wait_returns_none_when_no_headers():
    class FakeResponse:
        headers = {}

    assert utils._extract_rate_limit_wait(FakeResponse()) is None


# ---------------------------------------------------------------------------
# Mastodon X-RateLimit-Reset integration via retry_with_backoff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_429_uses_x_ratelimit_reset_header(monkeypatch):
    """Mastodon sends X-RateLimit-Reset; decorator should honour it."""
    sleep_calls = _make_sleep_capture(monkeypatch)

    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now
        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(retry, "datetime", FakeDatetime)

    class FakeResponse:
        status_code = 429
        headers = {"x-ratelimit-reset": str(pinned_now.timestamp() + 75)}

    class Fake429Error(Exception):
        response = FakeResponse()

    @utils.retry_with_backoff
    async def always_429():
        raise Fake429Error()

    with pytest.raises(Fake429Error):
        await always_429()

    assert sleep_calls[0] == pytest.approx(75.0, abs=1.0)
