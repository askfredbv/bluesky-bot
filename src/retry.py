"""Retry/backoff machinery: classify an error as rate-limit vs transient,
parse Retry-After / X-RateLimit-Reset headers, and sleep with the right budget.

Rate-limit and transient errors get independent retry budgets so a burst of 429s
cannot starve the transient-error allowance. Extracted from src/utils.py;
imports nothing from utils.
"""
import asyncio
import functools
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from src.config import (
    BACKOFF_FACTOR,
    JITTER_RANGE,
    MAX_API_RETRIES,
    RATE_LIMIT_BASE_WAIT_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
)
from src.logger import SafeLogger


def classify_retry(exception: Exception) -> str:
    """Return 'rate_limit' for HTTP 429 errors, 'transient' otherwise.

    Duck-typed on `exception.response.status_code` so the classifier works for
    atproto's RequestException and any httpx-style error without importing
    either SDK into utils.py.
    """
    response = getattr(exception, "response", None)
    status = getattr(response, "status_code", None)
    if status == 429:
        return "rate_limit"
    return "transient"
def _parse_retry_after_header(raw: Any) -> Optional[float]:
    """Parse a Bluesky-style `Retry-After` header value.

    Accepts seconds-as-number or an HTTP-date (RFC 7231 §7.1.3). Returns the
    number of seconds to wait, or None if the value cannot be parsed. A
    negative delta (HTTP-date already in the past) is clamped to 0.
    """
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)
def _parse_ratelimit_reset_header(raw: Any) -> Optional[float]:
    """Parse a Mastodon-style `X-RateLimit-Reset` header value.

    Accepts either a unix timestamp (seconds-since-epoch) or an ISO-8601
    timestamp. Returns the number of seconds until the reset, or None if the
    value cannot be parsed. Past resets clamp to 0.
    """
    if raw is None:
        return None
    now_ts = datetime.now(timezone.utc).timestamp()
    try:
        reset_ts = float(raw)
        # Plausibility: unix timestamps in 2025+ are ~1.7e9; tiny values
        # are more likely seconds-until-reset than epoch-seconds.
        if reset_ts > 1_000_000_000:
            return max(0.0, reset_ts - now_ts)
        return max(0.0, reset_ts)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
def _extract_rate_limit_wait(response: Any) -> Optional[float]:
    """Pull a wait-time (seconds) from response headers. None if unusable.

    Checks, in order:
      - `retry-after` (Bluesky / generic HTTP)
      - `x-ratelimit-reset` (Mastodon)
      - `ratelimit-reset` (legacy fallback)
    """
    headers = getattr(response, "headers", {}) or {}
    # Normalise to lowercase-keyed lookups without assuming dict subtype
    def _get(name: str) -> Any:
        if hasattr(headers, "get"):
            val = headers.get(name)
            if val is not None:
                return val
            return headers.get(name.lower()) or headers.get(name.title())
        return None

    raw_retry_after = _get("retry-after")
    parsed = _parse_retry_after_header(raw_retry_after)
    if parsed is not None:
        return parsed

    raw_reset = _get("x-ratelimit-reset") or _get("ratelimit-reset")
    return _parse_ratelimit_reset_header(raw_reset)
async def sleep_for_rate_limit(attempt: int, exception: Exception, function: str = "unknown") -> None:
    """Sleep appropriately for a 429; raise when the retry budget is exhausted.

    `attempt` is 1-indexed (1 = first retry). Raises the provided exception
    once `attempt > RATE_LIMIT_MAX_RETRIES`.
    """
    if attempt > RATE_LIMIT_MAX_RETRIES:
        SafeLogger.error(
            "rate_limit_exhausted",
            "Rate limit retries exhausted",
            exception=exception,
            attempt=attempt,
            max_attempts=RATE_LIMIT_MAX_RETRIES,
            function=function,
        )
        raise exception

    response = getattr(exception, "response", None)
    header_wait = _extract_rate_limit_wait(response)
    if header_wait is not None:
        wait_time = header_wait
        header_used = True
    else:
        wait_time = float(RATE_LIMIT_BASE_WAIT_SECONDS * attempt)
        header_used = False

    SafeLogger.warn(
        "rate_limit_hit",
        "Rate limit (429); backing off",
        attempt=attempt,
        max_attempts=RATE_LIMIT_MAX_RETRIES,
        wait_seconds=round(wait_time),
        function=function,
        header_used=header_used,
    )
    await asyncio.sleep(wait_time)
async def sleep_for_transient(attempt: int, exception: Exception, function: str = "unknown") -> None:
    """Sleep with exponential backoff for transient errors; raise on exhaustion.

    `attempt` is 1-indexed. Raises the provided exception once
    `attempt > MAX_API_RETRIES`.
    """
    if attempt > MAX_API_RETRIES:
        SafeLogger.error(
            "retry_exhausted",
            "Ultimate failure after retry attempts",
            exception=exception,
            attempt=attempt,
            max_attempts=MAX_API_RETRIES,
            function=function,
        )
        raise exception

    wait_time = (BACKOFF_FACTOR ** attempt) + random.uniform(0, JITTER_RANGE)
    SafeLogger.warn(
        "retry_scheduled",
        "Retry scheduled with backoff",
        attempt=attempt,
        max_attempts=MAX_API_RETRIES,
        function=function,
        wait_seconds=round(wait_time, 2),
    )
    await asyncio.sleep(wait_time)
def retry_with_backoff(func):
    """Decorator to retry an async function, branching by error class.

    Thin wrapper over `classify_retry` + `sleep_for_rate_limit` /
    `sleep_for_transient`. Rate-limit and transient errors get independent
    retry budgets so a rate-limited run doesn't burn its transient budget.
    Total attempts: 1 initial + up-to-MAX_..._RETRIES retries.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        rate_limit_attempts = 0
        transient_attempts = 0
        while True:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if classify_retry(e) == "rate_limit":
                    rate_limit_attempts += 1
                    await sleep_for_rate_limit(rate_limit_attempts, e, function=func.__name__)
                else:
                    transient_attempts += 1
                    await sleep_for_transient(transient_attempts, e, function=func.__name__)
    return wrapper
