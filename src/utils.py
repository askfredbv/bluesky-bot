import os
import json
import re
import random
import requests
import feedparser
import socket
from datetime import datetime, timezone, timedelta
from PIL import Image
import io
import google.generativeai as genai
from src.config import RSS_FEEDS, SEEN_FILE, REPLIED_FILE

socket.setdefaulttimeout(15)

def load_seen_articles():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading seen articles: {e}")
    return []

def save_seen_articles(seen_links):
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump(seen_links, f, indent=2)
    except Exception as e:
        print(f"Error saving seen articles: {e}")

def load_replied_to():
    if os.path.exists(REPLIED_FILE):
        try:
            with open(REPLIED_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading replied state: {e}")
    return []

def save_replied_to(replied_ids):
    try:
        with open(REPLIED_FILE, "w") as f:
            json.dump(replied_ids, f, indent=2)
    except Exception as e:
        print(f"Error saving replied state: {e}")

def fetch_news(seen_links, limit=5):
    print("Fetching news from RSS feeds...")
    all_items = []
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(days=2)

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if not pub_date or pub_date > lookback:
                    summary = entry.summary if hasattr(entry, 'summary') else ""
                    clean_summary = re.sub('<[^<]+?>', '', summary)[:300]
                    all_items.append({
                        "title": entry.title,
                        "description": clean_summary,
                        "link": entry.link,
                        "source": feed.feed.title if hasattr(feed.feed, 'title') else url
                    })
        except Exception as e:
            print(f"Error parsing feed {url}: {e}")
            
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
    unseen_items.sort(key=lambda x: x['is_scholar_gem'], reverse=True)
    return unseen_items[:limit]

def generate_image(api_key, prompt):
    print(f"Generating image with DALL-E 3: {prompt[:50]}...")
    url = "https://api.openai.com/v1/images/generations"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"model": "dall-e-3", "prompt": prompt, "n": 1, "size": "1024x1024"}
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    image_url = response.json()['data'][0]['url']
    return requests.get(image_url).content

def compress_image(image_bytes):
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()
