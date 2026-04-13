import os
import sys
import random
from datetime import datetime, timezone
import google.generativeai as genai
from dotenv import load_dotenv

# Internal Imports
from src.config import (
    RSS_FEEDS, APPROVED_BIO_BSKY, APPROVED_BIO_MASTODON,
    MAX_POST_LENGTH_BSKY, MAX_GENERATION_RETRIES
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

load_dotenv()

def get_recent_posts(client, handle):
    try:
        response = client.app.bsky.feed.get_author_feed(actor=handle, limit=10)
        return [p.post.record.text for p in response.feed if hasattr(p.post.record, 'text')]
    except Exception as e:
        print(f"Error fetching recent posts: {e}")
        return []

def update_live_status(mode, signal_strength="High (Scholar)"):
    """v4.2 Pro Feature: Automatically update the README dashboard."""
    try:
        with open("README.md", "r") as f:
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
        
        with open("README.md", "w") as f:
            f.writelines(new_lines)
        print("Updated README Live Status Dashboard.")
    except Exception as e:
        print(f"Failed to update status dashboard: {e}")

def main():
    print("--- Daily Poster Engine v4.2 Internal Startup ---")
    
    # Environment Check
    api_keys = {
        "gemini": os.environ.get("GEMINI_API_KEY"),
        "bsky_user": os.environ.get("BLUESKY_USERNAME", "askfred.be"),
        "bsky_pass": os.environ.get("BLUESKY_APP_PASSWORD") or os.environ.get("BLUESKY_PASSWORD"),
        "openai": os.environ.get("OPENAI_API_KEY"),
        "masto_token": os.environ.get("MASTODON_ACCESS_TOKEN"),
        "masto_url": os.environ.get("MASTODON_API_BASE_URL") or "https://mastodon.social"
    }

    if not all([api_keys["gemini"], api_keys["bsky_user"], api_keys["bsky_pass"]]):
        print("Error: Missing core credentials.")
        sys.exit(1)

    # 1. Determine Mode
    current_hour = datetime.now(timezone.utc).hour
    mode = "curator" if current_hour < 11 else "mentor"
    print(f"Slot Triggered: {mode.upper()} mode.")

    # 2. State & Context
    seen_links = load_seen_articles()
    
    # 3. Mode-specific Logic
    news_items = []
    if mode == "curator":
        news_items = fetch_news(seen_links)
        if not news_items:
            print("No fresh news. Falling back to Mentor mode.")
            mode = "mentor"

    # 4. Generate Content
    # We briefly log in to get recent posts for deduplication
    temp_client = post_to_bluesky(api_keys["bsky_user"], api_keys["bsky_pass"], ["Initial Handshake..."])
    recent_posts = get_recent_posts(temp_client, api_keys["bsky_user"])
    
    content_list, chosen_topic = generate_content(api_keys["gemini"], recent_posts, mode=mode, news_items=news_items)

    # 5. Image Injection
    image_data = None
    image_alt = "AI generated clinical tech visual"
    if api_keys["openai"] and random.random() < (0.3 if mode == "mentor" else 0.1):
        print("Generating visual asset...")
        genai.configure(api_key=api_keys["gemini"])
        vision_model = genai.GenerativeModel('gemini-1.5-flash')
        prompt_instr = f"Create a minimalist DALL-E 3 prompt for: '{content_list[0]}'. Dark mode, tech/mentor vibe."
        image_prompt = vision_model.generate_content(prompt_instr).text.strip()
        image_alt = vision_model.generate_content(f"Write 10-word alt text for: {image_prompt}").text.strip()
        try:
            image_data = compress_image(generate_image(api_keys["openai"], image_prompt))
        except Exception as e: print(f"Visual failed: {e}")

    # 6. Broadcasting
    print("Initiating Multi-Channel Broadcast...")
    # Bluesky
    client = post_to_bluesky(api_keys["bsky_user"], api_keys["bsky_pass"], content_list, image_data, image_alt)
    # Mastodon
    post_to_mastodon(api_keys["masto_token"], api_keys["masto_url"], content_list, image_data, image_alt)
    
    # 7. Post-Broadcast Actions
    handle_interactions(client, api_keys["bsky_user"], api_keys["gemini"])
    update_profile_bio(client, APPROVED_BIO_BSKY)
    update_profile_bio_mastodon(api_keys["masto_token"], api_keys["masto_url"], APPROVED_BIO_MASTODON)
    
    # 8. Update State & Dashboard
    if mode == "curator" and news_items:
        seen_links.extend([i['link'] for i in news_items[:5]])
        save_seen_articles(seen_links)
    
    update_live_status(mode)
    print("--- Broadcast Complete ---")

if __name__ == "__main__":
    main()
