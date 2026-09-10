from datetime import datetime, timezone

import pytest

from src import retry
from src.config import RATE_LIMIT_BASE_WAIT_SECONDS, RATE_LIMIT_MAX_RETRIES, MAX_API_RETRIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sleep_capture(monkeypatch):
    """Patch asyncio.sleep on retry and return a list that records every call."""
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)

    monkeypatch.setattr(retry.asyncio, "sleep", fake_sleep)
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

# These used to reach sleep_for_rate_limit / sleep_for_transient only through
# the retry_with_backoff decorator, which production stopped using in 185aad4 and
# which was removed on 2026-09-10. They were also the only tests of the
# no-header base-wait branch, so they now call the live functions directly.

@pytest.mark.asyncio
async def test_rate_limit_wait_is_at_least_base_seconds_without_a_header(monkeypatch):
    """No Retry-After / X-RateLimit-Reset header: every rate-limit retry waits at
    least RATE_LIMIT_BASE_WAIT_SECONDS (scaled by the attempt number)."""
    sleep_calls = _make_sleep_capture(monkeypatch)
    Fake429Error = _make_429_error()

    for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
        await retry.sleep_for_rate_limit(attempt, Fake429Error())

    assert len(sleep_calls) == RATE_LIMIT_MAX_RETRIES
    assert all(s >= RATE_LIMIT_BASE_WAIT_SECONDS for s in sleep_calls)


@pytest.mark.asyncio
async def test_rate_limit_wait_uses_the_retry_after_header(monkeypatch):
    """When a Retry-After header is present its value is used as the wait time."""
    sleep_calls = _make_sleep_capture(monkeypatch)
    Fake429Error = _make_429_error(retry_after="90")

    await retry.sleep_for_rate_limit(1, Fake429Error())

    assert sleep_calls == [90.0]


@pytest.mark.asyncio
async def test_rate_limit_budget_raises_once_exhausted(monkeypatch):
    """The rate-limit path has its own budget, RATE_LIMIT_MAX_RETRIES: the retry
    after that re-raises the original error instead of sleeping again."""
    sleep_calls = _make_sleep_capture(monkeypatch)
    Fake429Error = _make_429_error()

    with pytest.raises(Fake429Error):
        await retry.sleep_for_rate_limit(RATE_LIMIT_MAX_RETRIES + 1, Fake429Error())

    assert sleep_calls == []


# ---------------------------------------------------------------------------
# Transient (non-429) path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transient_wait_is_short_exponential_backoff(monkeypatch):
    """Non-429 errors use the short exponential backoff, well under the rate-limit wait."""
    sleep_calls = _make_sleep_capture(monkeypatch)

    for attempt in range(1, MAX_API_RETRIES + 1):
        await retry.sleep_for_transient(attempt, ConnectionError("timeout"))

    assert len(sleep_calls) == MAX_API_RETRIES
    assert all(s < RATE_LIMIT_BASE_WAIT_SECONDS for s in sleep_calls)


@pytest.mark.asyncio
async def test_transient_budget_raises_once_exhausted(monkeypatch):
    """Non-429 errors exhaust MAX_API_RETRIES and then re-raise the original error."""
    sleep_calls = _make_sleep_capture(monkeypatch)

    with pytest.raises(RuntimeError):
        await retry.sleep_for_transient(MAX_API_RETRIES + 1, RuntimeError("generic failure"))

    assert sleep_calls == []


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
    assert retry.classify_retry(_exc_with_status(429)) == "rate_limit"


def test_classify_retry_returns_transient_for_500():
    assert retry.classify_retry(_exc_with_status(500)) == "transient"


def test_classify_retry_returns_transient_when_no_response_attr():
    assert retry.classify_retry(ConnectionError("timeout")) == "transient"


def test_classify_retry_returns_transient_when_status_is_none():
    class FakeResponse:
        status_code = None
        headers = {}

    class FakeError(Exception):
        response = FakeResponse()

    assert retry.classify_retry(FakeError()) == "transient"


# ---------------------------------------------------------------------------
# Header normalisation
# ---------------------------------------------------------------------------

def test_parse_retry_after_accepts_seconds():
    assert retry._parse_retry_after_header("90") == 90.0


def test_parse_retry_after_accepts_http_date(monkeypatch):
    # HTTP-date 60 seconds in the future from a pinned "now"
    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now

    monkeypatch.setattr(retry, "datetime", FakeDatetime)
    result = retry._parse_retry_after_header("Wed, 22 Apr 2026 12:01:00 GMT")
    assert result is not None
    assert 59.0 <= result <= 61.0


def test_parse_retry_after_returns_none_for_garbage():
    assert retry._parse_retry_after_header("not-a-thing") is None


def test_parse_retry_after_clamps_past_date_to_zero(monkeypatch):
    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now

    monkeypatch.setattr(retry, "datetime", FakeDatetime)
    assert retry._parse_retry_after_header("Wed, 22 Apr 2026 11:59:00 GMT") == 0.0


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
    assert retry._parse_ratelimit_reset_header(str(reset)) == pytest.approx(120.0, abs=1.0)


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
    result = retry._parse_ratelimit_reset_header("2026-04-22T12:02:00+00:00")
    assert result == pytest.approx(120.0, abs=1.0)


def test_parse_ratelimit_reset_returns_none_for_garbage():
    assert retry._parse_ratelimit_reset_header("garbage") is None


def test_extract_rate_limit_wait_prefers_retry_after():
    class FakeResponse:
        headers = {"retry-after": "45", "x-ratelimit-reset": "999999"}

    assert retry._extract_rate_limit_wait(FakeResponse()) == 45.0


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

    assert retry._extract_rate_limit_wait(FakeResponse()) == pytest.approx(30.0, abs=1.0)


def test_extract_rate_limit_wait_returns_none_when_no_headers():
    class FakeResponse:
        headers = {}

    assert retry._extract_rate_limit_wait(FakeResponse()) is None


# ---------------------------------------------------------------------------
# Mastodon X-RateLimit-Reset honoured by sleep_for_rate_limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_429_uses_x_ratelimit_reset_header(monkeypatch):
    """Mastodon sends X-RateLimit-Reset; sleep_for_rate_limit honours it."""
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

    await retry.sleep_for_rate_limit(1, Fake429Error())

    assert sleep_calls[0] == pytest.approx(75.0, abs=1.0)


# ---------------------------------------------------------------------------
# Header forms the 2026-09-10 mutation run found untested
# ---------------------------------------------------------------------------

def _pin_now(monkeypatch):
    pinned_now = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return pinned_now

        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

    monkeypatch.setattr(retry, "datetime", FakeDatetime)
    return pinned_now


def test_ratelimit_reset_small_number_is_seconds_until_reset():
    # Below 1e9 the value is read as seconds-until-reset, not as an epoch.
    assert retry._parse_ratelimit_reset_header("30") == 30.0


def test_ratelimit_reset_negative_seconds_clamp_to_zero():
    assert retry._parse_ratelimit_reset_header("-5") == 0.0


def test_ratelimit_reset_epoch_in_the_past_clamps_to_zero(monkeypatch):
    pinned_now = _pin_now(monkeypatch)
    assert retry._parse_ratelimit_reset_header(str(pinned_now.timestamp() - 60)) == 0.0


def test_ratelimit_reset_iso_without_timezone_is_read_as_utc(monkeypatch):
    _pin_now(monkeypatch)
    assert retry._parse_ratelimit_reset_header("2026-04-22T12:02:00") == pytest.approx(120.0, abs=1.0)


def test_retry_after_http_date_with_unknown_zone_is_read_as_utc(monkeypatch):
    # RFC 5322 "-0000" means "zone unknown": parsedate_to_datetime returns a
    # naive datetime for it, which must not crash the subtraction.
    _pin_now(monkeypatch)
    assert retry._parse_retry_after_header("Wed, 22 Apr 2026 12:01:00 -0000") == pytest.approx(60.0, abs=1.0)


def test_retry_after_is_found_under_a_title_case_header_name():
    # A plain dict keyed "Retry-After", not a case-insensitive mapping.
    class FakeResponse:
        headers = {"Retry-After": "45"}

    assert retry._extract_rate_limit_wait(FakeResponse()) == 45.0
