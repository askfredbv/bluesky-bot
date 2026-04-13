import os
import sys
import random
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from dotenv import load_dotenv

# Internal Imports
from src.config import (
    SEEN_FILE, APPROVED_BIO_BSKY, APPROVED_BIO_MASTODON,
    MAX_POST_LENGTH_BSKY
)
from src.utils import (
    load_seen_articles, save_seen_articles, fetch_news,
    generate_image, compress_image
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
        print("Updated README Live Status Dashboard.")
    except Exception as e:
        SafeLogger.error("Failed to update status dashboard", e)

async def main():
    print("--- Daily Poster Engine v4.4 Fortress (Async) ---")
    
    # Environment Check
    creds = {
        "gemini": os.environ.get("GEMINI_API_KEY"),
        "bsky_user": os.environ.get("BLUESKY_USERNAME", "askfred.be"),
        "bsky_pass": os.environ.get("BLUESKY_APP_PASSWORD") or os.environ.get("BLUESKY_PASSWORD"),
        "openai": os.environ.get("OPENAI_API_KEY"),
        "masto_token": os.environ.get("MASTODON_ACCESS_TOKEN"),
        "masto_url": os.environ.get("MASTODON_API_BASE_URL") or "https://mastodon.social"
    }

    if not all([creds["gemini"], creds["bsky_user"], creds["bsky_pass"]]):
        print("Error: Missing core credentials.")
        sys.exit(1)

    # 1. Slot Strategy
    current_hour = datetime.now(timezone.utc).hour
    mode = "curator" if current_hour < 11 else "mentor"
    print(f"Slot Triggered: {mode.upper()} mode.")

    # 2. Context Initialization (Silent Handshake)
    seen_links = load_seen_articles()
    
    # Mode-specific Fetch
    news_items = []
    if mode == "curator":
        news_items = await fetch_news(seen_links)
        if not news_items:
            print("No fresh research. Falling back to Mentor mode.")
            mode = "mentor"

    # Fetch context from Bsky silently - this client will also be reused for broadcasting
    from atproto import AsyncClient
    bsky_client = AsyncClient()
    await bsky_client.login(creds["bsky_user"], creds["bsky_pass"])
    recent_posts = await get_recent_posts(bsky_client, creds["bsky_user"])
    
    # 3. Content Synthesis
    content_list, chosen_topic = await generate_content(creds["gemini"], recent_posts, mode=mode, news_items=news_items)

    # 4. Asynchronous Image Generation (Parallel with logic if needed, but sequential for prompt dependency)
    image_data = None
    image_alt = "AI generated technical synthesis visual"
    if creds["openai"] and random.random() < (0.3 if mode == "mentor" else 0.1):
        try:
            image_data = await generate_image(creds["openai"], f"Minimalist professional tech banner for: {content_list[0]}")
            image_data = compress_image(image_data)
        except Exception as e:
            SafeLogger.error("Visual asset generation failed", e)

    # 5. Elite Parallel Broadcasting
    print(f"Initiating Concurrent Delivery to {creds['masto_url']} and Bluesky...")
    
    # We run both broadcasts in parallel
    broadcast_tasks = [
        post_to_bluesky(creds["bsky_user"], creds["bsky_pass"], content_list, image_data, image_alt),
        post_to_mastodon(creds["masto_token"], creds["masto_url"], content_list, image_data, image_alt)
    ]
    
    # Gather results
    results = await asyncio.gather(*broadcast_tasks, return_exceptions=True)
    
    # Identify the Bsky client for interactions (reuse the pre-authenticated client)
    bsky_broadcast_client = results[0] if not isinstance(results[0], Exception) else bsky_client

    # 6. Post-Run Automation
    automation_tasks = [
        handle_interactions(bsky_broadcast_client, creds["bsky_user"], creds["gemini"]),
        update_profile_bio(bsky_broadcast_client, APPROVED_BIO_BSKY),
        update_profile_bio_mastodon(creds["masto_token"], creds["masto_url"], APPROVED_BIO_MASTODON),
        update_live_status(mode)
    ]
    await asyncio.gather(*automation_tasks, return_exceptions=True)

    # 7. Persistence
    if mode == "curator" and news_items:
        # Avoid growing the file too large - prune to last 200 items
        seen_links = (seen_links + [i['link'] for i in news_items[:5]])[-200:]
        save_seen_articles(seen_links)

    print(f"--- [v4.3 Elite] Broadcast Cycle Complete in {mode} mode ---")

if __name__ == "__main__":
    asyncio.run(main())
