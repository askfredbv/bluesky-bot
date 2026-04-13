import json
import re
import asyncio
import httpx
import feedparser
import socket
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from PIL import Image
import io
from src.config import RSS_FEEDS, SEEN_FILE, REPLIED_FILE
from src.logger import SafeLogger

socket.setdefaulttimeout(15)

def load_seen_articles() -> List[str]:
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            SafeLogger.error("Failed to load seen articles", e)
    return []

def save_seen_articles(seen_links: List[str]) -> None:
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(seen_links, f, indent=2)
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

async def fetch_single_feed(client: httpx.AsyncClient, url: str) -> List[Dict[str, Any]]:
    try:
        response = await client.get(url, timeout=10.0)
        feed = feedparser.parse(response.text)
        items = []
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=2)
        
        for entry in feed.entries:
            pub_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if not pub_date or pub_date > lookback:
                summary = entry.summary if hasattr(entry, 'summary') else ""
                clean_summary = re.sub('<[^<]+?>', '', summary)[:300]
                items.append({
                    "title": entry.title,
                    "description": clean_summary,
                    "link": entry.link,
                    "source": feed.feed.title if hasattr(feed.feed, 'title') else url
                })
        return items
    except Exception as e:
        SafeLogger.error(f"Error parsing feed {url}", e)
        return []

async def fetch_news(seen_links: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    print(f"Fetching news from {len(RSS_FEEDS)} RSS feeds concurrently...")
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [fetch_single_feed(client, url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks)
    
    all_items = [item for sublist in results for item in sublist]
    
    processed_items = []
    for item in all_items:
        source_link = item.get('link', '')
        is_scholar_gem = "arxiv.org" in source_link
        processed_items.append({
            'title': item.get('title', 'No Title'),
            'summary': item.get('description', 'No summary available.'),
            'link': source_link,
            'source': 'arXiv' if is_scholar_gem else 'Tech News',
            'is_scholar_gem': is_scholar_gem
        })

    unseen_items = [i for i in processed_items if i['link'] not in seen_links]
    # Deduplicate by link
    unique_unseen = {i['link']: i for i in unseen_items}.values()
    
    sorted_items = sorted(unique_unseen, key=lambda x: x['is_scholar_gem'], reverse=True)
    return list(sorted_items)[:limit]

async def generate_image(api_key: str, prompt: str) -> bytes:
    print(f"Generating image with DALL-E 3 (Async): {prompt[:50]}...")
    url = "https://api.openai.com/v1/images/generations"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"model": "dall-e-3", "prompt": prompt, "n": 1, "size": "1024x1024"}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=data, timeout=60.0)
        response.raise_for_status()
        image_url = response.json()['data'][0]['url']
        img_response = await client.get(image_url, timeout=60.0)
        return img_response.content

def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()
