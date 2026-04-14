import json
import re
import random
import asyncio
import os
import httpx
import feedparser
import socket
import functools
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, TypeVar
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import io
from PIL import Image
import fcntl
from src.config import (
    RSS_FEEDS, SEEN_FILE, REPLIED_FILE, 
    MAX_API_RETRIES, BACKOFF_FACTOR, JITTER_RANGE,
    SOURCE_TIERS, PRODUCT_KEYWORDS, GROUNDBREAKING_KEYWORDS,
    TOPIC_MAP, HIDDEN_GEM_SOURCES
)
from src.logger import SafeLogger

socket.setdefaulttimeout(15)
T = TypeVar("T")

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
                    SafeLogger.error(f"Ultimate failure in {func.__name__} after {MAX_API_RETRIES} attempts.", e)
                    raise e
                wait_time = (BACKOFF_FACTOR ** retries) + random.uniform(0, JITTER_RANGE)
                SafeLogger.warn(f"Retry {retries}/{MAX_API_RETRIES} for {func.__name__} in {wait_time:.2f}s...")
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
        SafeLogger.error(f"Corrupt JSON detected in {file_path}", e)
        restored_data = None
        if backup_path.exists():
            try:
                with open(backup_path, "r") as backup_file:
                    restored_data = json.load(backup_file)
                _atomic_write_json(file_path, restored_data)
                SafeLogger.warn(f"Restored {file_path} from backup {backup_path}")
            except Exception as backup_error:
                SafeLogger.error(f"Backup restore failed for {file_path}", backup_error)

        if restored_data is None:
            corrupt_copy = file_path.with_suffix(file_path.suffix + ".corrupt")
            try:
                os.replace(file_path, corrupt_copy)
                SafeLogger.warn(f"Moved corrupt file to {corrupt_copy}")
            except Exception as move_error:
                SafeLogger.error(f"Failed moving corrupt file for {file_path}", move_error)
            _atomic_write_json(file_path, default_data)
            return default_data
        data = restored_data
    except Exception as e:
        SafeLogger.error(f"Failed to load JSON from {file_path}", e)
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
    data = _load_json_with_repair(
        SEEN_FILE,
        lambda: default,
        migrate_list_to_seen_shape=True
    )
    if isinstance(data, dict) and "links" in data and "recent_topics" in data:
        return data
    SafeLogger.warn("Unexpected seen_articles format detected; repairing to default shape.")
    _atomic_write_json(SEEN_FILE, default)
    return default

def save_seen_articles(seen_data: Dict[str, Any]) -> None:
    try:
        _atomic_write_json(SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".bak"), seen_data)
        _atomic_write_json(SEEN_FILE, seen_data)
    except Exception as e:
        SafeLogger.error("Failed to save seen articles", e)

def load_replied_to() -> List[str]:
    data = _load_json_with_repair(REPLIED_FILE, lambda: [])
    if isinstance(data, list):
        return data
    SafeLogger.warn("Unexpected replied_to format detected; repairing to default shape.")
    _atomic_write_json(REPLIED_FILE, [])
    return []

def save_replied_to(replied_ids: List[str]) -> None:
    try:
        _atomic_write_json(REPLIED_FILE.with_suffix(REPLIED_FILE.suffix + ".bak"), replied_ids)
        _atomic_write_json(REPLIED_FILE, replied_ids)
    except Exception as e:
        SafeLogger.error("Failed to save replied state", e)

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
        SafeLogger.warn(f"Failed to open image for compression: {e}")
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
    SafeLogger.warn(f"Could not normalise relative URL without base: {url}")
    return None

async def get_link_metadata(url: str) -> Dict[str, Any]:
    """Scrapes OpenGraph metadata from a URL (v4.5 Sage replacement for DALL-E)."""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.get(url, headers=headers)
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
                    img_res = await client.get(img_url, timeout=5.0)
                    if img_res.status_code == 200:
                        img_data = img_res.content
                        if len(img_data) > 900 * 1024:
                            print(f"Compressing large OpenGraph image ({len(img_data)//1024}KB)...")
                            img_data = compress_image(img_data)

            return {
                "title": og_title['content'] if og_title else soup.title.string if soup.title else "Technical Insight",
                "description": og_description['content'][:200] if og_description else "",
                "image_data": img_data,
                "url": url
            }
    except Exception as e:
        SafeLogger.error(f"Metadata extraction failed for {url}", e)
        return {"title": "Source Link", "description": "", "image_data": None, "url": url}

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
        response = await client.get(url, timeout=10.0)
        feed = feedparser.parse(response.text)
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
                SafeLogger.warn(f"Skipping feed entry with unusable link: '{raw_link}'")
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
    except Exception as e:
        SafeLogger.error(f"Error parsing feed {url}", e)
        return []

async def fetch_news(seen_links: List[str], recent_topics: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    """Weighted asynchronous fetch with Hidden Gem injection (v4.5 Sage)."""
    print(f"Fetching news from {len(RSS_FEEDS)} feeds with Sage Intelligence...")
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [fetch_single_feed(client, url) for url in RSS_FEEDS]
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
                print(f"Injecting Hidden Gem: {ranked[i]['title'][:40]}...")
                top_candidates[limit-1] = ranked[i] # Swap last spot for the Gem
                break
                
    return top_candidates
