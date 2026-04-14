import os
import sys
import asyncio
import random
from datetime import datetime, timezone
from typing import List, Optional
from dotenv import load_dotenv

# Internal Imports
from src.config import (
    SEEN_FILE, APPROVED_BIO_BSKY, APPROVED_BIO_MASTODON,
    MAX_POST_LENGTH_BSKY, POST_JITTER_MIN_SECONDS, POST_JITTER_MAX_SECONDS,
    THREAD_PAUSE_PROFILES, DEFAULT_THREAD_PAUSE_PROFILE,
    PROFILE_BIO_UPDATE_COOLDOWN_HOURS
)
from src.utils import (
    load_seen_articles, update_seen_articles, fetch_news,
    get_link_metadata, should_update_profile_bio, mark_profile_bio_updated
)
from src.agents import generate_content, handle_interactions
from src.broadcasters import (
    post_to_bluesky, post_to_mastodon,
    update_profile_bio, update_profile_bio_mastodon
)
from src.logger import SafeLogger

load_dotenv()

async def get_recent_posts(client, handle: str) -> List[str]:
    """Fetched recent posts silently from a logged-in client."""
    try:
        response = await client.app.bsky.feed.get_author_feed(actor=handle, limit=10)
        return [p.post.record.text for p in response.feed if hasattr(p.post.record, 'text')]
    except Exception as e:
        SafeLogger.error("Failed to fetch recent posts", e)
        return []

async def update_live_status(mode: str, signal_strength: str = "Elite (Async)"):
    """v4.3 Elite Feature: Automatically update the README dashboard."""
    try:
        readme_path = os.path.join(os.getcwd(), "README.md")
        with open(readme_path, "r") as f:
            lines = f.readlines()
        
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        icon = "☕" if mode == "curator" else "💡"
        
        new_lines = []
        for line in lines:
            if "| **Broadcaster** |" in line:
                new_lines.append(f"| **Broadcaster** | Operational | {today} | {icon} {mode.capitalize()} |\n")
            elif "| **Signal Strength** |" in line:
                new_lines.append(f"| **Signal Strength** | {signal_strength} | -- | -- |\n")
            else:
                new_lines.append(line)
        
        with open(readme_path, "w") as f:
            f.writelines(new_lines)
    except Exception as e:
        SafeLogger.error("Failed to update status dashboard", e)

async def apply_humanized_post_delay():
    """Inject pre-post timing jitter so runs are less clock-perfect."""
    if POST_JITTER_MAX_SECONDS <= 0:
        return
    lower = max(0, POST_JITTER_MIN_SECONDS)
    upper = max(lower, POST_JITTER_MAX_SECONDS)
    wait_seconds = random.randint(lower, upper)
    if wait_seconds == 0:
        return
    print(f"Humanized delay before posting: {wait_seconds}s")
    await asyncio.sleep(wait_seconds)

async def main():
    print("--- AskFred Engine v4.6 Resilience Upgrade ---")
    
    # Environment Check
    creds = {
        "gemini": os.environ.get("GEMINI_API_KEY"),
        "bsky_user": os.environ.get("BLUESKY_USERNAME", "askfred.be"),
        "bsky_pass": os.environ.get("BLUESKY_APP_PASSWORD") or os.environ.get("BLUESKY_PASSWORD"),
        "masto_token": os.environ.get("MASTODON_ACCESS_TOKEN"),
        "masto_url": os.environ.get("MASTODON_API_BASE_URL") or "https://mastodon.social"
    }

    if not all([creds["gemini"], creds["bsky_user"], creds["bsky_pass"]]):
        print("Error: Missing core credentials.")
        sys.exit(1)

    # 1. Slot Strategy
    current_hour = datetime.now(timezone.utc).hour
    mode = "curator" if current_hour < 11 else "mentor"
    
    # 2. State & Context (Topic Memory)
    seen_data = load_seen_articles()
    
    # 3. Weighted Curation
    news_items = []
    link_meta = None
    if mode == "curator":
        news_items = await fetch_news(seen_data["links"], seen_data["recent_topics"])
        if len(news_items) < 3:
            print(f"Low high-signal news volume ({len(news_items)}). Shifting to Strategist mode.")
            mode = "strategist"
        else:
            # Sage 4.5: Scrape metadata for the top item for 'Rich Link Previews'
            link_meta = await get_link_metadata(news_items[0]['link'])

    # Silent handshake for Bsky context
    from atproto import AsyncClient
    bsky_client = AsyncClient()
    await bsky_client.login(creds["bsky_user"], creds["bsky_pass"])
    recent_posts = await get_recent_posts(bsky_client, creds["bsky_user"])
    
    # 4. Content Synthesis (Temporal Aware)
    content_list, chosen_topic = await generate_content(creds["gemini"], recent_posts, mode=mode, news_items=news_items)
    thread_pause_profile = random.choice(list(THREAD_PAUSE_PROFILES.keys())) if THREAD_PAUSE_PROFILES else DEFAULT_THREAD_PAUSE_PROFILE
    await apply_humanized_post_delay()

    # 5. Sage Parallel Broadcasting
    print(f"Initiating Concurrent Delivery to Bluesky and Mastodon...")
    broadcast_tasks = [
        post_to_bluesky(
            creds["bsky_user"], creds["bsky_pass"], content_list, link_meta,
            thread_pause_profile=thread_pause_profile
        ),
        post_to_mastodon(
            creds["masto_token"], creds["masto_url"], content_list,
            thread_pause_profile=thread_pause_profile
        )
    ]
    
    results = await asyncio.gather(*broadcast_tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            SafeLogger.error("Broadcast task failed", r)
            
    bsky_broadcast_client = results[0] if not isinstance(results[0], Exception) else bsky_client

    # 6. Post-Run Automation
    should_update_bsky_bio = should_update_profile_bio(
        "bluesky", APPROVED_BIO_BSKY, PROFILE_BIO_UPDATE_COOLDOWN_HOURS
    )
    should_update_masto_bio = should_update_profile_bio(
        "mastodon", APPROVED_BIO_MASTODON, PROFILE_BIO_UPDATE_COOLDOWN_HOURS
    )

    automation_tasks = [
        handle_interactions(bsky_broadcast_client, creds["bsky_user"], creds["gemini"]),
        update_live_status(mode)
    ]
    if should_update_bsky_bio:
        automation_tasks.append(update_profile_bio(bsky_broadcast_client, APPROVED_BIO_BSKY))
    if should_update_masto_bio:
        automation_tasks.append(update_profile_bio_mastodon(creds["masto_token"], creds["masto_url"], APPROVED_BIO_MASTODON))
    await asyncio.gather(*automation_tasks, return_exceptions=True)
    if should_update_bsky_bio:
        mark_profile_bio_updated("bluesky", APPROVED_BIO_BSKY)
    if should_update_masto_bio:
        mark_profile_bio_updated("mastodon", APPROVED_BIO_MASTODON)

    # 7. Knowledge Persistence
    if mode == "curator" and news_items:
        seen_data["links"] = (seen_data["links"] + [i['link'] for i in news_items])[-200:]
        
        # Sense topic to update memory
        topic_cat = news_items[0].get('detected_topic', 'General')
        if topic_cat != 'General':
            seen_data["recent_topics"] = (seen_data["recent_topics"] + [topic_cat])[-5:]
            
        update_seen_articles(lambda _: seen_data)

    print(f"--- [v4.6 Resilience] Intelligence Cycle Complete ({mode}) ---")

if __name__ == "__main__":
    asyncio.run(main())
