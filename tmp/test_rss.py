import feedparser
from datetime import datetime, timezone, timedelta
import time
import re

RSS_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
]

def fetch_news(seen_links) -> list[dict]:
    print("Testing news fetch...")
    all_entries = []
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(days=2)

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            print(f"Parsed {url}, found {len(feed.entries)} entries.")
            for entry in feed.entries[:3]: # Just first 3 for test
                if entry.link in seen_links:
                    continue
                all_entries.append({
                    "title": entry.title,
                    "link": entry.link,
                    "source": feed.feed.title if hasattr(feed.feed, 'title') else url
                })
        except Exception as e:
            print(f"Error: {e}")
    return all_entries

print("Starting RSS test...")
news = fetch_news([])
for n in news[:5]:
    print(f"- {n['title']} [{n['source']}]")
