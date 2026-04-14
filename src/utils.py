import json
import re
import random
import asyncio
import httpx
import feedparser
import socket
import functools
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
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
    FEED_MAX_KEEPALIVE_CONNECTIONS
)
from src.logger import SafeLogger

socket.setdefaulttimeout(15)

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

def load_seen_articles() -> Dict[str, Any]:
    """Load seen state including links and recent topics."""
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list): # Migration from old format
                    return {"links": data, "recent_topics": []}
                return data
        except Exception as e:
            SafeLogger.error("Failed to load seen articles", e)
    return {"links": [], "recent_topics": []}

def save_seen_articles(seen_data: Dict[str, Any]) -> None:
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(seen_data, f, indent=2)
    except Exception as e:
        SafeLogger.error("Failed to save seen articles", e)

def load_replied_to() -> List[str]:
    if REPLIED_FILE.exists():
        try:
            with open(REPLIED_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            SafeLogger.error("Failed to load replied state", e)
    return []

def save_replied_to(replied_ids: List[str]) -> None:
    try:
        with open(REPLIED_FILE, "w") as f:
            json.dump(replied_ids, f, indent=2)
    except Exception as e:
        SafeLogger.error("Failed to save replied state", e)

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
        response = await client.get(url)
        feed = feedparser.parse(response.text)
        if feed.bozo:
            SafeLogger.warn(
                f"event=feed_parse_failure url={url} parse_error={type(feed.bozo_exception).__name__}"
            )
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
    except httpx.TimeoutException as e:
        SafeLogger.warn(f"event=feed_timeout url={url} error={type(e).__name__}")
        return []
    except Exception as e:
        SafeLogger.warn(f"event=feed_fetch_failure url={url} error={type(e).__name__}")
        return []

async def fetch_news(seen_links: List[str], recent_topics: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    """Weighted asynchronous fetch with Hidden Gem injection (v4.5 Sage)."""
    print(f"Fetching news from {len(RSS_FEEDS)} feeds with Sage Intelligence...")

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
                print(f"Injecting Hidden Gem: {ranked[i]['title'][:40]}...")
                top_candidates[limit-1] = ranked[i] # Swap last spot for the Gem
                break
                
    return top_candidates
