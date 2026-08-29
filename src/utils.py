import json
import re
import random
import asyncio
import os
import tempfile
import httpx
import feedparser
import socket
import functools
import ipaddress
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, TypeVar, Tuple
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse, urlunparse
from bs4 import BeautifulSoup
import io
from PIL import Image
from src.config import (
    RSS_FEEDS, SEEN_FILE, REPLIED_FILE,
    MAX_API_RETRIES, BACKOFF_FACTOR, JITTER_RANGE,
    SOURCE_TIERS, PRODUCT_KEYWORDS, GROUNDBREAKING_KEYWORDS,
    TOPIC_MAP, HIDDEN_GEM_SOURCES,
    FEED_FETCH_CONCURRENCY,
    FEED_REQUEST_CONNECT_TIMEOUT_SECONDS,
    FEED_REQUEST_READ_TIMEOUT_SECONDS,
    FEED_REQUEST_WRITE_TIMEOUT_SECONDS,
    FEED_REQUEST_POOL_TIMEOUT_SECONDS,
    FEED_MAX_CONNECTIONS,
    FEED_MAX_KEEPALIVE_CONNECTIONS,
    METADATA_FETCH_ALLOWED_DOMAINS,
    METADATA_FETCH_BLOCKED_DOMAINS,
    FEED_SUMMARY_MAX_CHARS,
    CONSENSUS_SYNERGY_BONUS,
    RATE_LIMIT_BASE_WAIT_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
    GENERIC_IMAGE_PATTERNS,
    MOMENTUM_PRODUCTS,
    MOMENTUM_PRODUCT_BONUS,
    PIONEER_COOLDOWN_DAYS,
)
from src.logger import SafeLogger
from src.file_lock import file_lock

# Decompression-bomb guard (process-wide Pillow setting). The bot opens remote,
# attacker-influenceable images via Pillow — OpenGraph thumbnails from article
# URLs and generated post images. A malicious feed could serve a tiny file that
# declares enormous dimensions; without a cap, decoding it OOMs the runner.
# Pillow's default (~89M px) is generous for a social bot; 10M px comfortably
# covers any legitimate post image and blocks the absurd sizes. Pillow raises
# DecompressionBombError at open() from the header dimensions, which the guarded
# Image.open call sites already catch.
Image.MAX_IMAGE_PIXELS = 10_000_000

T = TypeVar("T")
STATE_STORE_TIMEOUT_SECONDS = 10.0

def _state_store_url_for_key(key: str) -> Optional[str]:
    base_url = os.environ.get("STATE_STORE_URL", "").strip()
    if not base_url:
        return None
    if "{key}" in base_url:
        return base_url.format(key=key)
    return f"{base_url.rstrip('/')}/{key}"

def _state_store_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    token = os.environ.get("STATE_STORE_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

def _load_state_from_store(key: str) -> Optional[Any]:
    endpoint = _state_store_url_for_key(key)
    if not endpoint:
        return None
    try:
        response = httpx.get(endpoint, headers=_state_store_headers(), timeout=STATE_STORE_TIMEOUT_SECONDS)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "value" in data:
            return data["value"]
        return data
    except Exception as e:
        SafeLogger.warn("state_store_read_failed", "Remote state read failed", error_type=type(e).__name__, state_key=key)
        return None

def _save_state_to_store(key: str, data: Any) -> bool:
    endpoint = _state_store_url_for_key(key)
    if not endpoint:
        return False
    payload = {"value": data}
    try:
        response = httpx.put(
            endpoint,
            headers={**_state_store_headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=STATE_STORE_TIMEOUT_SECONDS
        )
        if response.status_code not in (200, 201, 204, 405):
            response.raise_for_status()
            return True
        if response.status_code in (200, 201, 204):
            return True
    except Exception as e:
        SafeLogger.warn("state_store_put_failed", "Remote state PUT failed", error_type=type(e).__name__, state_key=key)

    try:
        response = httpx.post(
            endpoint,
            headers={**_state_store_headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=STATE_STORE_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.status_code in (200, 201, 204)
    except Exception as e:
        SafeLogger.warn("state_store_post_failed", "Remote state POST failed", error_type=type(e).__name__, state_key=key)
        return False

def _load_gist_state_strict(filename: str) -> Tuple[Optional[Any], bool]:
    """Load a JSON state file from a private Gist, distinguishing a
    TRUSTWORTHY empty from an UNTRUSTED one.

    Returns ``(value, trusted)``:
      - ``trusted=True``  — the result is safe to act on. Either the file
        loaded fine (value = parsed JSON), or the read genuinely found no
        such state (value = None) because no Gist is configured (local dev)
        or the Gist is reachable but the file has never been written.
      - ``trusted=False`` — the read itself failed (transport / HTTP / auth /
        corrupt-content). value = None, but that None means "could not read",
        NOT "no state exists". Callers must NOT overwrite real state on this.

    Why this matters: ``_load_gist_state`` collapses every failure to ``None``,
    so a transient Gist read failure is indistinguishable from genuinely-absent
    state. For append-only / approval state (``pending_replies.json``) that
    ambiguity is destructive — a save after a failed read overwrites real
    pending/posted/rejected history. This strict variant lets those callers
    skip work instead. (Flagged by the Codex review on 2026-06-12; see
    AGENTS.md "fail loud on critical state".)
    """
    gist_id = os.environ.get("GIST_ID", "").strip()
    if not gist_id:
        return None, True  # local dev, no Gist configured — empty is legitimate
    token = os.environ.get("GIST_TOKEN", "").strip()
    headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers,
            timeout=STATE_STORE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        # Transport / HTTP / auth failure — the Gist itself was unreachable.
        # This is the untrusted case: state may well exist, we just couldn't
        # read it. Do NOT let callers treat this as "empty".
        SafeLogger.warn(
            "gist_state_read_failed",
            "Gist state read failed",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
            state_file=filename,
        )
        return None, False
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict) or filename not in files:
        # Gist reachable, but this file has never been written. Genuinely
        # absent — trustworthy empty (e.g. the first proactive run).
        return None, True
    try:
        return json.loads(files[filename]["content"]), True
    except Exception as e:
        # Stored content is corrupt/unparseable. Treat as untrusted rather
        # than silently clobbering it with an empty state.
        SafeLogger.warn(
            "gist_state_parse_failed",
            "Gist state content could not be parsed",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
            state_file=filename,
        )
        return None, False


def _load_gist_state(filename: str) -> Optional[Any]:
    """Load a JSON state file from a private GitHub Gist.

    Thin wrapper over ``_load_gist_state_strict`` that drops the trust flag —
    preserves the original "None on any failure or absence" contract for
    callers (seen_articles, etc.) that have their own multi-tier fallbacks.
    State that must NOT be overwritten on a failed read should call
    ``_load_gist_state_strict`` directly and honour ``trusted``.
    """
    return _load_gist_state_strict(filename)[0]


def _save_gist_state(filename: str, data: Any) -> bool:
    """Save a JSON state file to a private GitHub Gist."""
    gist_id = os.environ.get("GIST_ID", "").strip()
    if not gist_id:
        return False
    token = os.environ.get("GIST_TOKEN", "").strip()
    headers: Dict[str, str] = {"Accept": "application/vnd.github+json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers,
            json={"files": {filename: {"content": json.dumps(data, indent=2)}}},
            timeout=STATE_STORE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        # v4.19 (2026-05-13): promoted WARN → ERROR per the 2026-04-22 retro
        # callback. Silent state-persistence failures are worse than noisy ones.
        # The bot continues (returns False, caller falls back to local file),
        # but the run's log now shows ERROR-level events surfaced in the
        # GitHub Actions UI — same visibility as broadcast_invariant_violated
        # and content_generation_exhausted. The retro's other mitigation
        # ("surface count in weekly digest") still applies once Phase 2 lands;
        # this is the cheap defensible interim.
        SafeLogger.error(
            "gist_state_save_failed",
            "Gist state save failed",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
            state_file=filename,
        )
        return False


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

def _atomic_write_json(file_path: Path, data: Any) -> None:
    """Atomically write JSON by replacing target with a temp file in same dir."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path_str = tempfile.mkstemp(prefix=f".{file_path.name}.", suffix=".tmp", dir=file_path.parent)
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, file_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

def _load_json_with_repair(
    file_path: Path,
    default_factory: Callable[[], T],
    *,
    migrate_list_to_seen_shape: bool = False
) -> T:
    """
    Load JSON with corruption repair.
    - If decode fails, try restoring from .bak.
    - If backup is also invalid/missing, preserve corrupt file and reset to default.
    """
    default_data = default_factory()
    if not file_path.exists():
        return default_data

    backup_path = file_path.with_suffix(file_path.suffix + ".bak")
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        SafeLogger.error("json_corrupt_detected", "Corrupt JSON detected", exception=e, file_path=str(file_path))
        restored_data = None
        if backup_path.exists():
            try:
                with open(backup_path, "r") as backup_file:
                    restored_data = json.load(backup_file)
                _atomic_write_json(file_path, restored_data)
                SafeLogger.warn("json_restored_from_backup", "Restored file from backup", file_path=str(file_path), backup_path=str(backup_path))
            except Exception as backup_error:
                SafeLogger.error("json_backup_restore_failed", "Backup restore failed", exception=backup_error, file_path=str(file_path))

        if restored_data is None:
            import time as _time
            corrupt_copy = file_path.parent / f"{file_path.name}.corrupt.{int(_time.time())}"
            moved = False
            try:
                os.replace(file_path, corrupt_copy)
                moved = True
                SafeLogger.warn("json_corrupt_moved", "Moved corrupt file", file_path=str(file_path), corrupt_copy=str(corrupt_copy))
            except Exception as move_error:
                SafeLogger.error("json_corrupt_move_failed", "Failed moving corrupt file", exception=move_error, file_path=str(file_path))
            if moved:
                _atomic_write_json(file_path, default_data)
            return default_data
        data = restored_data
    except Exception as e:
        SafeLogger.error("json_load_failed", "Failed to load JSON", exception=e, file_path=str(file_path))
        return default_data

    if migrate_list_to_seen_shape and isinstance(data, list):
        migrated = {"links": data, "recent_topics": [], "pioneer_recent": []}
        _atomic_write_json(file_path, migrated)
        return migrated  # type: ignore[return-value]

    return data


def prune_pioneer_recent(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop pioneer_recent entries older than PIONEER_COOLDOWN_DAYS.

    Tolerant of malformed entries (missing keys, unparseable dates) — they're
    dropped silently. Idempotent.
    """
    if not entries:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=PIONEER_COOLDOWN_DAYS)
    kept = []
    for entry in entries:
        if not isinstance(entry, dict) or "id" not in entry or "posted_at" not in entry:
            continue
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            continue
        if posted_at >= cutoff:
            kept.append(entry)
    return kept

def _file_lock(lock_path: Path):
    """Context manager for an advisory process lock."""
    return file_lock(lock_path)

def _ensure_pioneer_field(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure pioneer_recent key exists. In-place add for old state shapes."""
    if "pioneer_recent" not in data:
        data["pioneer_recent"] = []
    return data


def load_seen_articles() -> Dict[str, Any]:
    """Load seen state including links, recent topics, and pioneer cooldown."""
    default = {"links": [], "recent_topics": [], "pioneer_recent": []}
    # 1. Try Gist
    gist_data = _load_gist_state("seen_articles.json")
    if isinstance(gist_data, dict) and "links" in gist_data and "recent_topics" in gist_data:
        return _ensure_pioneer_field(gist_data)
    # 2. Existing STATE_STORE_URL fallback
    remote_data = _load_state_from_store("seen_articles")
    if isinstance(remote_data, dict) and "links" in remote_data and "recent_topics" in remote_data:
        return _ensure_pioneer_field(remote_data)
    if isinstance(remote_data, list):
        migrated = {"links": remote_data, "recent_topics": [], "pioneer_recent": []}
        _save_state_to_store("seen_articles", migrated)
        return migrated
    # 3. Local file fallback
    data = _load_json_with_repair(
        SEEN_FILE,
        lambda: default,
        migrate_list_to_seen_shape=True
    )
    if isinstance(data, dict) and "links" in data and "recent_topics" in data:
        return _ensure_pioneer_field(data)
    SafeLogger.warn("seen_articles_format_repaired", "Unexpected seen_articles format detected; repairing to default shape")
    _atomic_write_json(SEEN_FILE, default)
    return default

def save_seen_articles(seen_data: Dict[str, Any]) -> None:
    if _save_gist_state("seen_articles.json", seen_data):
        return
    if _save_state_to_store("seen_articles", seen_data):
        return
    try:
        _atomic_write_json(SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".bak"), seen_data)
        _atomic_write_json(SEEN_FILE, seen_data)
    except Exception as e:
        SafeLogger.error("seen_articles_save_failed", "Failed to save seen articles", exception=e)

def load_replied_to() -> List[str]:
    # 1. Try Gist
    gist_data = _load_gist_state("replied_to.json")
    if isinstance(gist_data, list):
        return gist_data
    # 2. Existing STATE_STORE_URL fallback
    remote_data = _load_state_from_store("replied_to")
    if isinstance(remote_data, list):
        return remote_data
    # 3. Local file fallback
    data = _load_json_with_repair(REPLIED_FILE, lambda: [])
    if isinstance(data, list):
        return data
    SafeLogger.warn("replied_to_format_repaired", "Unexpected replied_to format detected; repairing to default shape")
    _atomic_write_json(REPLIED_FILE, [])
    return []

def save_replied_to(replied_ids: List[str]) -> None:
    if _save_gist_state("replied_to.json", replied_ids):
        return
    if _save_state_to_store("replied_to", replied_ids):
        return
    try:
        _atomic_write_json(REPLIED_FILE.with_suffix(REPLIED_FILE.suffix + ".bak"), replied_ids)
        _atomic_write_json(REPLIED_FILE, replied_ids)
    except Exception as e:
        SafeLogger.error("replied_to_save_failed", "Failed to save replied state", exception=e)

def update_seen_articles(mutator: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
    """Lock-protected read-modify-write for seen_articles.json."""
    lock_path = SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".lock")
    with _file_lock(lock_path):
        current = load_seen_articles()
        updated = mutator(current)
        save_seen_articles(updated)
    return updated

def update_replied_to(mutator: Callable[[List[str]], List[str]]) -> List[str]:
    """Lock-protected read-modify-write for replied_to.json."""
    lock_path = REPLIED_FILE.with_suffix(REPLIED_FILE.suffix + ".lock")
    with _file_lock(lock_path):
        current = load_replied_to()
        updated = mutator(current)
        save_replied_to(updated)
    return updated

def compress_image(image_bytes: bytes, max_size_kb: int = 900) -> bytes:
    """Compresses an image to stay under AtProto's 1MB blob limit."""
    try:
        img_io = io.BytesIO(image_bytes)
        img = Image.open(img_io)
    except Exception as e:
        SafeLogger.warn("image_open_for_compression_failed", "Failed to open image for compression", error_type=type(e).__name__)
        return image_bytes

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    quality = 90
    out_io = io.BytesIO()
    img.save(out_io, format="JPEG", quality=quality)
    
    while out_io.tell() > max_size_kb * 1024 and quality > 10:
        quality -= 10
        out_io = io.BytesIO()
        img.save(out_io, format="JPEG", quality=quality)

    return out_io.getvalue()

_ARXIV_CANONICAL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)


def canonical_url(url: str) -> str:
    """Canonicalise a URL for deduplication.

    arXiv: abs/pdf/html forms with or without version suffix collapse to
    'arxiv:<id>'. Everything else: https scheme, lowercased host, query and
    fragment stripped, trailing slash removed. Path case preserved per RFC 3986.
    Returns empty string for empty input; returns the stripped input unchanged
    when parsing fails or no host is present.
    """
    if not url:
        return ""
    trimmed = url.strip()
    match = _ARXIV_CANONICAL_RE.search(trimmed)
    if match:
        return f"arxiv:{match.group(1)}"
    try:
        parsed = urlparse(trimmed)
    except Exception:
        return trimmed
    if not parsed.netloc:
        return trimmed
    return urlunparse((
        "https",
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        "",
        "",
    ))


def normalise_url(url: str, base_url: str = "") -> Optional[str]:
    """
    Normalises a raw URL extracted from HTML into a fully qualified URL.
    Handles three cases that would otherwise crash httpx:
      - Protocol-relative: //cdn.example.com/img.jpg  -> https://cdn.example.com/img.jpg
      - Relative path:     /images/hero.jpg           -> https://example.com/images/hero.jpg
      - Already absolute:  https://example.com/img    -> unchanged
    Returns None if the URL is empty or unfixable.
    """
    if not url:
        return None
    url = url.strip()
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith(('http://', 'https://')):
        return url
    if base_url:
        return urljoin(base_url, url)
    # Can't resolve a relative URL without a base — skip it
    SafeLogger.warn("relative_url_without_base", "Could not normalise relative URL without base", url=url)
    return None

def is_safe_public_url(url: str) -> bool:
    """
    Validates that a URL is public and safe to request.
    Rejects non-HTTP(S), localhost-like hosts, and private/non-routable IPs.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    if hostname.lower() in {"localhost"}:
        return False

    return _resolve_public_ip_candidates(hostname) is not None

def _is_public_ip(ip_str: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    return not (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )

def _resolve_public_ip_candidates(hostname: str) -> Optional[List[str]]:
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None
    except Exception:
        return None

    candidate_ips: List[str] = []
    seen: set[str] = set()
    for result in resolved:
        ip_str = result[4][0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        if not _is_public_ip(ip_str):
            return None
        candidate_ips.append(ip_str)

    return candidate_ips or None

def _hostname_matches_policy(hostname: str, domain_rule: str) -> bool:
    normalized_hostname = hostname.lower().strip(".")
    normalized_rule = domain_rule.lower().strip().strip(".")
    if not normalized_rule:
        return False
    return normalized_hostname == normalized_rule or normalized_hostname.endswith(f".{normalized_rule}")

def is_allowed_metadata_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False

    if any(_hostname_matches_policy(hostname, domain) for domain in METADATA_FETCH_BLOCKED_DOMAINS):
        SafeLogger.warn("domain_policy_blocked", "Blocked URL by metadata domain policy denylist", url=url, hostname=hostname)
        return False

    if METADATA_FETCH_ALLOWED_DOMAINS and not any(
        _hostname_matches_policy(hostname, domain) for domain in METADATA_FETCH_ALLOWED_DOMAINS
    ):
        SafeLogger.warn("domain_policy_blocked", "Blocked URL by metadata domain policy allowlist", url=url, hostname=hostname)
        return False

    return True

@contextmanager
def _resolver_pinned_to_ips(hostname: str, allowed_ips: List[str]):
    """
    Temporarily constrains DNS resolution for one hostname to a prevalidated set.
    If the resolver returns addresses outside the allowed set, resolution is blocked.
    """
    original_getaddrinfo = socket.getaddrinfo
    canonical_hostname = hostname.lower()
    allowed_set = set(allowed_ips)

    def guarded_getaddrinfo(host: str, *args, **kwargs):
        if str(host).lower() != canonical_hostname:
            return original_getaddrinfo(host, *args, **kwargs)

        current = original_getaddrinfo(host, *args, **kwargs)
        current_ips = {entry[4][0] for entry in current}
        if not current_ips:
            raise socket.gaierror(f"No DNS records found for {host}")

        unexpected = current_ips - allowed_set
        if unexpected:
            raise socket.gaierror(f"Resolver returned unexpected address for {host}")

        filtered = [entry for entry in current if entry[4][0] in allowed_set]
        if not filtered:
            raise socket.gaierror(f"No allowed DNS records found for {host}")
        return filtered

    socket.getaddrinfo = guarded_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo

async def get_with_safe_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    max_redirects: int = 5
) -> Optional[httpx.Response]:
    """
    Fetch a URL while validating each redirect target and disallowing scheme changes.
    """
    current_url = url
    initial_scheme = urlparse(url).scheme

    for _ in range(max_redirects + 1):
        if not is_allowed_metadata_fetch_url(current_url):
            return None

        parsed_current = urlparse(current_url)
        hostname = parsed_current.hostname
        if not hostname:
            SafeLogger.warn("unsafe_url_blocked", "Blocked unsafe URL request", url=current_url)
            return None

        candidate_ips = _resolve_public_ip_candidates(hostname)
        if not candidate_ips:
            SafeLogger.warn("unsafe_url_blocked", "Blocked unsafe URL request", url=current_url)
            return None

        try:
            with _resolver_pinned_to_ips(hostname, candidate_ips):
                response = await client.get(
                    current_url,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=False
                )
        except Exception as e:
            SafeLogger.warn("dns_validated_request_blocked", "Blocked URL request after DNS validation", url=current_url, error_type=type(e).__name__)
            return None

        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                return response
            next_url = urljoin(str(response.url), location)

            parsed_next = urlparse(next_url)
            if parsed_next.scheme != initial_scheme:
                SafeLogger.warn("cross_scheme_redirect_blocked", "Blocked cross-scheme redirect", from_url=current_url, to_url=next_url)
                return None

            if not is_allowed_metadata_fetch_url(next_url):
                return None

            if not is_safe_public_url(next_url):
                SafeLogger.warn("unsafe_redirect_target_blocked", "Blocked unsafe redirect target", url=next_url)
                return None

            current_url = next_url
            continue

        return response

    SafeLogger.warn("too_many_redirects_blocked", "Blocked URL due to too many redirects", url=url)
    return None

async def get_link_metadata(url: str) -> Dict[str, Any]:
    """Scrapes OpenGraph metadata from a URL (v4.5 Sage replacement for DALL-E)."""
    fallback = {"title": "Source Link", "description": "", "image_data": None, "url": url}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    if not is_safe_public_url(url):
        SafeLogger.warn("unsafe_article_url_blocked", "Blocked unsafe article URL", url=url)
        return fallback
    if not is_allowed_metadata_fetch_url(url):
        return fallback

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await get_with_safe_redirects(client, url, headers=headers, timeout=10.0)
            if response is None:
                return fallback
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            og_title = soup.find("meta", property="og:title")
            og_description = soup.find("meta", property="og:description")
            og_image = soup.find("meta", property="og:image")
            
            # Download image if exists for uploading as blob
            img_data = None
            if og_image and og_image.get('content'):
                img_url = normalise_url(og_image['content'], base_url=url)
                if img_url:
                    if not is_safe_public_url(img_url):
                        SafeLogger.warn("unsafe_og_image_url_blocked", "Blocked unsafe og:image URL", url=img_url)
                    elif not is_allowed_metadata_fetch_url(img_url):
                        SafeLogger.warn("domain_policy_blocked", "Blocked og:image by metadata domain policy", url=img_url)
                    elif any(p in img_url.lower() for p in GENERIC_IMAGE_PATTERNS):
                        SafeLogger.info("generic_logo_skipped", "Skipping generic logo thumbnail", url=img_url)
                    else:
                        img_res = await get_with_safe_redirects(client, img_url, timeout=5.0)
                        if img_res and img_res.status_code == 200:
                            img_data = img_res.content
                            if len(img_data) > 900 * 1024:
                                SafeLogger.info("og_image_compression_started", "Compressing large OpenGraph image", size_kb=len(img_data)//1024)
                                img_data = compress_image(img_data)

            return {
                "title": og_title['content'] if og_title else soup.title.string if soup.title else "Technical Insight",
                "description": og_description['content'][:200] if og_description else "",
                "image_data": img_data,
                "url": url
            }
    except Exception as e:
        SafeLogger.error("metadata_extraction_failed", "Metadata extraction failed", exception=e, url=url)
        return fallback

def calculate_relevance_score(item: Dict[str, Any], pub_date: datetime, recent_topics: List[str]) -> float:
    """Calculates a weighted 6-factor score (source tier, product signals, groundbreaking keywords, time decay, topic diversity, consensus synergy)."""
    score = 0.0
    text = f"{item['title']} {item['description']}".lower()
    
    # 1. Source Tier — guard against relative or malformed links
    try:
        parsed = urlparse(item['link'])
        source_domain = parsed.netloc if parsed.netloc else ""
        score += next((val for domain, val in SOURCE_TIERS.items() if domain in source_domain), 3.0)
    except Exception:
        score += 3.0  # Default tier score if link is unparseable
    
    # 2. Product Boost
    if any(kw in text for kw in PRODUCT_KEYWORDS): score += 5.0

    # 2b. Momentum Product Boost — flagship 2026 models score higher than generic product news
    if any(p in text for p in MOMENTUM_PRODUCTS): score += MOMENTUM_PRODUCT_BONUS

    # 3. Groundbreaking Tech Boost
    if any(kw in text for kw in GROUNDBREAKING_KEYWORDS): score += 7.0
    
    # 4. Time Decay (Lose 0.5 point per hour)
    age_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
    score -= (age_hours * 0.5)
    
    # 5. Topic Diversity Penalty
    item_topic = "General"
    for topic, kws in TOPIC_MAP.items():
        if any(kw in text for kw in kws):
            item_topic = topic
            break
    
    if item_topic in recent_topics:
        score -= 12.0 # Heavy "Discernment" penalty for repetition

    # 6. Consensus Synergy: reward stories covered by multiple independent feeds
    feed_count = len(item.get('source_feeds', []))
    if feed_count > 1:
        score += CONSENSUS_SYNERGY_BONUS * (feed_count - 1)

    item['detected_topic'] = item_topic
    return score

async def fetch_single_feed(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: Optional[httpx.Timeout] = None
) -> "FeedFetchResult":  # noqa: F821 — forward ref, imported lazily inside the function to break the src.metrics ↔ src.utils circular dependency
    """Fetch and normalise one RSS feed; return a structured outcome.

    The `ok` flag reflects whether the HTTP request succeeded (i.e. no
    transport error). Parse failures flip `ok=False` via the bozo path;
    a feed that responds but has zero entries is `ok=True` with both
    `entries_total` and `entries_accepted` at zero.
    """
    # Local import to avoid circular dependency at module load time.
    from src.metrics import FeedFetchResult

    try:
        response = await client.get(url, timeout=timeout)
        feed = feedparser.parse(response.text)
        bozo_error_type: Optional[str] = None
        if feed.bozo:
            bozo_exception = getattr(feed, 'bozo_exception', None)
            bozo_error_type = type(bozo_exception).__name__ if bozo_exception else "UnknownParseError"
            SafeLogger.warn(
                "feed_parse_failure",
                "Feed parse failure",
                url=url,
                error_type=bozo_error_type,
            )

        raw_entries = getattr(feed, 'entries', []) or []
        entries_total = len(raw_entries)

        items: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=2)

        for entry in raw_entries:
            time_struct = entry.get('published_parsed') or entry.get('updated_parsed')
            if not time_struct:
                continue

            import calendar
            pub_date = datetime.fromtimestamp(calendar.timegm(time_struct), timezone.utc)
            if pub_date <= lookback:
                continue

            # Normalise the article link — some feeds serve relative URLs
            raw_link = entry.get('link', '').strip()
            normalised_link = normalise_url(raw_link, base_url=url)
            if not normalised_link:
                SafeLogger.warn("feed_entry_skipped_bad_link", "Skipping feed entry with unusable link", raw_link=raw_link)
                continue

            summary = entry.get('summary', entry.get('description', ""))
            clean_summary = re.sub('<[^<]+?>', '', summary)[:FEED_SUMMARY_MAX_CHARS]
            items.append({
                "title": entry.title,
                "description": clean_summary,
                "link": normalised_link,
                "pub_date": pub_date,
                "source_feeds": [url],
            })
        # Request succeeded; bozo-parse still counts as ok=True for feed
        # health because the fetch itself worked — bozo is a soft signal
        # and often transient (malformed <br> tags etc.).
        return FeedFetchResult(
            url=url,
            ok=True,
            entries_total=entries_total,
            entries_accepted=len(items),
            error_type=bozo_error_type,
            entries=items,
        )
    except httpx.TimeoutException as e:
        SafeLogger.warn("feed_timeout", "Feed request timed out", url=url, error_type=type(e).__name__)
        return FeedFetchResult(url=url, ok=False, entries_total=0, entries_accepted=0, error_type=type(e).__name__)
    except Exception as e:
        SafeLogger.warn("feed_fetch_failure", "Feed fetch failed", url=url, error_type=type(e).__name__)
        return FeedFetchResult(url=url, ok=False, entries_total=0, entries_accepted=0, error_type=type(e).__name__)

async def fetch_news(seen_links: List[str], recent_topics: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    """Weighted asynchronous fetch with Hidden Gem injection (v4.5 Sage)."""
    SafeLogger.info("news_fetch_started", "Fetching news from configured feeds", feed_count=len(RSS_FEEDS))

    timeout = httpx.Timeout(
        connect=FEED_REQUEST_CONNECT_TIMEOUT_SECONDS,
        read=FEED_REQUEST_READ_TIMEOUT_SECONDS,
        write=FEED_REQUEST_WRITE_TIMEOUT_SECONDS,
        pool=FEED_REQUEST_POOL_TIMEOUT_SECONDS
    )
    limits = httpx.Limits(
        max_connections=FEED_MAX_CONNECTIONS,
        max_keepalive_connections=FEED_MAX_KEEPALIVE_CONNECTIONS
    )
    semaphore = asyncio.Semaphore(FEED_FETCH_CONCURRENCY)

    async def _fetch_with_semaphore(url: str):
        async with semaphore:
            return await fetch_single_feed(client, url, timeout=timeout)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        limits=limits
    ) as client:
        tasks = [_fetch_with_semaphore(url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks)

    # Record per-feed outcomes for the weekly health view.
    # Local import mirrors fetch_single_feed and keeps the circular-dep
    # resolution consistent.
    try:
        from src.metrics import load_feed_health, record_feed_attempt, save_feed_health
        feed_health = load_feed_health()
        for result in results:
            record_feed_attempt(feed_health, result)
        save_feed_health(feed_health)
    except Exception as e:
        SafeLogger.warn(
            "feed_health_record_failed",
            "Feed health telemetry skipped",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )

    all_raw = [item for result in results for item in result.entries]
    seen: dict = {}
    for item in all_raw:
        key = canonical_url(item['link'])
        if key in seen:
            seen[key]['source_feeds'] = list(set(seen[key]['source_feeds'] + item['source_feeds']))
        else:
            seen[key] = item
    seen_canonical = {canonical_url(link) for link in seen_links}
    unique_unseen = [i for i in seen.values() if canonical_url(i['link']) not in seen_canonical]
    
    # Apply Sage Scoring
    for item in unique_unseen:
        item['score'] = calculate_relevance_score(item, item['pub_date'], recent_topics)
    
    ranked = sorted(unique_unseen, key=lambda x: x['score'], reverse=True)
    
    # Hidden Gem Injection (Force at least one arXiv paper if none in top)
    top_candidates = ranked[:limit]
    has_gem = any(any(gem in i['link'] for gem in HIDDEN_GEM_SOURCES) for i in top_candidates)
    
    if not has_gem and len(ranked) > limit:
        for i in range(limit, len(ranked)):
            if any(gem in ranked[i]['link'] for gem in HIDDEN_GEM_SOURCES):
                SafeLogger.info("hidden_gem_injected", "Injecting hidden gem into top candidates", title_preview=ranked[i]['title'][:40])
                top_candidates[-1] = ranked[i]  # Swap last spot for the Gem
                break
                
    return top_candidates
