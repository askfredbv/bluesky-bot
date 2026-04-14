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
from typing import List, Dict, Any, Optional, Callable, TypeVar
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import io
from PIL import Image
import fcntl
from src.config import (
    RSS_FEEDS, SEEN_FILE, REPLIED_FILE, RUNTIME_STATE_FILE,
    MAX_API_RETRIES, BACKOFF_FACTOR, JITTER_RANGE,
    SOURCE_TIERS, PRODUCT_KEYWORDS, GROUNDBREAKING_KEYWORDS,
    TOPIC_MAP, HIDDEN_GEM_SOURCES,
    FEED_FETCH_CONCURRENCY,
    FEED_REQUEST_CONNECT_TIMEOUT_SECONDS,
    FEED_REQUEST_READ_TIMEOUT_SECONDS,
    FEED_REQUEST_WRITE_TIMEOUT_SECONDS,
    FEED_REQUEST_POOL_TIMEOUT_SECONDS,
    FEED_MAX_CONNECTIONS,
    FEED_MAX_KEEPALIVE_CONNECTIONS
)
from src.logger import SafeLogger

socket.setdefaulttimeout(15)
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

def retry_with_backoff(func):
    """Decorator to retry an async function with exponential backoff and jitter."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        retries = 0
        while retries < MAX_API_RETRIES:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                retries += 1
                if retries == MAX_API_RETRIES:
                    SafeLogger.error(
                        "retry_exhausted",
                        "Ultimate failure after retry attempts",
                        exception=e,
                        attempt=retries,
                        max_attempts=MAX_API_RETRIES,
                        function=func.__name__
                    )
                    raise e
                wait_time = (BACKOFF_FACTOR ** retries) + random.uniform(0, JITTER_RANGE)
                SafeLogger.warn(
                    "retry_scheduled",
                    "Retry scheduled with backoff",
                    attempt=retries,
                    max_attempts=MAX_API_RETRIES,
                    function=func.__name__,
                    wait_seconds=round(wait_time, 2)
                )
                await asyncio.sleep(wait_time)
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
            corrupt_copy = file_path.with_suffix(file_path.suffix + ".corrupt")
            try:
                os.replace(file_path, corrupt_copy)
                SafeLogger.warn("json_corrupt_moved", "Moved corrupt file", file_path=str(file_path), corrupt_copy=str(corrupt_copy))
            except Exception as move_error:
                SafeLogger.error("json_corrupt_move_failed", "Failed moving corrupt file", exception=move_error, file_path=str(file_path))
            _atomic_write_json(file_path, default_data)
            return default_data
        data = restored_data
    except Exception as e:
        SafeLogger.error("json_load_failed", "Failed to load JSON", exception=e, file_path=str(file_path))
        return default_data

    if migrate_list_to_seen_shape and isinstance(data, list):
        migrated = {"links": data, "recent_topics": []}
        _atomic_write_json(file_path, migrated)
        return migrated  # type: ignore[return-value]

    return data

def _file_lock(lock_path: Path):
    """Context manager for an advisory process lock."""
    class _Lock:
        def __enter__(self):
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = open(lock_path, "w")
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
            return self

        def __exit__(self, exc_type, exc, tb):
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            return False
    return _Lock()

def load_seen_articles() -> Dict[str, Any]:
    """Load seen state including links and recent topics."""
    default = {"links": [], "recent_topics": []}
    remote_data = _load_state_from_store("seen_articles")
    if isinstance(remote_data, dict) and "links" in remote_data and "recent_topics" in remote_data:
        return remote_data
    if isinstance(remote_data, list):
        migrated = {"links": remote_data, "recent_topics": []}
        _save_state_to_store("seen_articles", migrated)
        return migrated

    data = _load_json_with_repair(
        SEEN_FILE,
        lambda: default,
        migrate_list_to_seen_shape=True
    )
    if isinstance(data, dict) and "links" in data and "recent_topics" in data:
        return data
    SafeLogger.warn("seen_articles_format_repaired", "Unexpected seen_articles format detected; repairing to default shape")
    _atomic_write_json(SEEN_FILE, default)
    return default

def save_seen_articles(seen_data: Dict[str, Any]) -> None:
    if _save_state_to_store("seen_articles", seen_data):
        return
    try:
        _atomic_write_json(SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".bak"), seen_data)
        _atomic_write_json(SEEN_FILE, seen_data)
    except Exception as e:
        SafeLogger.error("seen_articles_save_failed", "Failed to save seen articles", exception=e)

def load_replied_to() -> List[str]:
    remote_data = _load_state_from_store("replied_to")
    if isinstance(remote_data, list):
        return remote_data

    data = _load_json_with_repair(REPLIED_FILE, lambda: [])
    if isinstance(data, list):
        return data
    SafeLogger.warn("replied_to_format_repaired", "Unexpected replied_to format detected; repairing to default shape")
    _atomic_write_json(REPLIED_FILE, [])
    return []

def save_replied_to(replied_ids: List[str]) -> None:
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

def load_runtime_state() -> Dict[str, Any]:
    """Load runtime metadata used for cadence gates and cooldowns."""
    default = {"profile_bio": {}}
    remote_data = _load_state_from_store("runtime_state")
    if isinstance(remote_data, dict) and "profile_bio" in remote_data:
        return remote_data

    data = _load_json_with_repair(RUNTIME_STATE_FILE, lambda: default)
    if isinstance(data, dict) and "profile_bio" in data:
        return data
    _atomic_write_json(RUNTIME_STATE_FILE, default)
    return default

def save_runtime_state(state: Dict[str, Any]) -> None:
    if _save_state_to_store("runtime_state", state):
        return
    try:
        _atomic_write_json(RUNTIME_STATE_FILE.with_suffix(RUNTIME_STATE_FILE.suffix + ".bak"), state)
        _atomic_write_json(RUNTIME_STATE_FILE, state)
    except Exception as e:
        SafeLogger.error("runtime_state_save_failed", "Failed to save runtime state", exception=e)

def update_runtime_state(mutator: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
    """Lock-protected read-modify-write for runtime_state.json."""
    lock_path = RUNTIME_STATE_FILE.with_suffix(RUNTIME_STATE_FILE.suffix + ".lock")
    with _file_lock(lock_path):
        current = load_runtime_state()
        updated = mutator(current)
        save_runtime_state(updated)
    return updated

def should_update_profile_bio(platform: str, desired_bio: str, cooldown_hours: int) -> bool:
    """Return True when bio text changed or cooldown has elapsed."""
    state = load_runtime_state()
    profile_data = state.get("profile_bio", {}).get(platform, {})
    last_bio = profile_data.get("last_bio", "")
    last_updated_raw = profile_data.get("updated_at")

    if last_bio != desired_bio:
        return True

    if not last_updated_raw:
        return True

    try:
        last_updated = datetime.fromisoformat(last_updated_raw)
    except Exception:
        return True

    return (datetime.now(timezone.utc) - last_updated) >= timedelta(hours=cooldown_hours)

def mark_profile_bio_updated(platform: str, bio_text: str) -> None:
    """Persist latest bio update metadata for cooldown checks."""
    now_iso = datetime.now(timezone.utc).isoformat()
    def _mutator(state: Dict[str, Any]) -> Dict[str, Any]:
        profile = state.setdefault("profile_bio", {})
        profile[platform] = {"last_bio": bio_text, "updated_at": now_iso}
        return state
    update_runtime_state(_mutator)

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
    """Calculates a weighted 5-factor score (Elite v4.5 pattern)."""
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
        
    item['detected_topic'] = item_topic
    return score

async def fetch_single_feed(client: httpx.AsyncClient, url: str) -> List[Dict[str, Any]]:
    try:
        response = await client.get(url)
        feed = feedparser.parse(response.text)
        if feed.bozo:
            SafeLogger.warn("feed_parse_failure", "Feed parse failure", url=url, error_type=type(feed.bozo_exception).__name__)
            return []
        items = []
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=2)
        
        for entry in feed.entries:
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
            clean_summary = re.sub('<[^<]+?>', '', summary)[:500]
            items.append({
                "title": entry.title,
                "description": clean_summary,
                "link": normalised_link,
                "pub_date": pub_date
            })
        return items
    except httpx.TimeoutException as e:
        SafeLogger.warn("feed_timeout", "Feed request timed out", url=url, error_type=type(e).__name__)
        return []
    except Exception as e:
        SafeLogger.warn("feed_fetch_failure", "Feed fetch failed", url=url, error_type=type(e).__name__)
        return []

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

    async def _fetch_with_semaphore(url: str) -> List[Dict[str, Any]]:
        async with semaphore:
            return await fetch_single_feed(client, url)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        limits=limits
    ) as client:
        tasks = [_fetch_with_semaphore(url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks)
    
    all_raw = [item for sublist in results for item in sublist]
    unique_unseen = [i for i in {i['link']: i for i in all_raw}.values() if i['link'] not in seen_links]
    
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
                top_candidates[limit-1] = ranked[i] # Swap last spot for the Gem
                break
                
    return top_candidates
