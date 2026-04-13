import os
import sys
import random
import io
import re
import json
import time
import socket
import feedparser
from datetime import datetime, timezone, timedelta
import google.generativeai as genai
from atproto import Client, models
from mastodon import Mastodon
from dotenv import load_dotenv
import requests
from PIL import Image

# Set network timeout and load environment
socket.setdefaulttimeout(15)
load_dotenv()

MAX_POST_LENGTH_BSKY = 300
MAX_POST_LENGTH_MASTODON = 500
MAX_GENERATION_RETRIES = 3
RECENT_POSTS_LIMIT = 20
SEEN_FILE = "seen_articles.json"
APPROVED_BIO_BSKY = """🤖 AskFred: The Multi-Channel Tech Mentor
Curated by Frederik Van Hecke. Always curious.

📰 Curation: AI & Tech news insights @ 08:00 UTC.
💡 Mentorship: IT leadership wisdom @ 14:00 UTC.

🚀 Working smarter, not harder.
🔗 askfred.be | frederikvanhecke.com"""

APPROVED_BIO_MASTODON = """💡 Your friendly IT Mentor in the trenches. Supporting work-life balance and continuous learning.

🌅 Morning tech curation | ☕ Afternoon IT advice. 
🚀 Helping you work smarter, not harder. 

🔗 askfred.be | frederikvanhecke.com"""

RSS_FEEDS = [
    "https://export.arxiv.org/rss/cs.AI",
    "https://export.arxiv.org/rss/cs.LG",
    "https://export.arxiv.org/rss/cs.RO",
    "https://openai.com/news/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "https://deepmind.google/blog/feed/",
    "https://simonwillison.net/atom/everything/",
    "https://engineering.fb.com/category/ml-ai/feed/",
    "https://arstechnica.com/tag/ai/feed/",
    "https://www.anthropic.com/news.rss",
    "https://the-decoder.com/feed/",
    "https://www.deeplearning.ai/the-batch/rss/",
    "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",
    "https://stability.ai/blog?format=rss",
    "https://siliconangle.com/category/ai/feed"
]



SECONDARY_TOPICS = [
    "mental health and burnout prevention",
    "open-source culture and community",
    "privacy and digital sovereignty",
    "sustainable tech practices",
    "remote work productivity",
    "creative hobbies and maker culture",
    "automation and scripting tips",
    "AI ethics and responsible use",
    "side projects and indie hacking",
    "continuous learning and skill building",
    "Linux and FOSS tools",
    "self-hosting and homelab adventures",
    "technical debt and code quality",
    "documentation and knowledge sharing",
    "cybersecurity basics for developers",
    "low-tech solutions to high-tech problems",
    "career growth and freelancing",
    "retro technology and computing history",
    "pair programming and code reviews",
    "asynchronous communication and deep work",
    "digital minimalism and focus",
    "curiosity and lifelong learning",
]

SYSTEM_INSTRUCTIONS_MENTOR = """
You are 'The Mentor' for askfred.be. Your voice is professional, positive, and human-centric.
You act as a friendly, experienced independent consultant sharing wisdom from the trenches of the IT world.

CORE VALUES:
- Work/Life Balance: Productivity isn't about working more; it's about working smarter.
- Continuous Learning: Tech moves fast; curiosity is your best tool.
- Human-First: Emphasize the people behind the code.

WRITING STYLE:
- Conversational but authoritative.
- Down-to-earth and slightly humorous.
- Avoid corporate buzzwords and robotic greetings.
- Use 1-2 relevant emojis. 
- Tone should be "warm professional."

ARCHITECTURE:
1. THE SPARK: Start with a relatable hook or a "did you know."
2. THE WISDOM: Provide one practical, actionable takeaway.
3. THE SPARKLE: End with a positive, encouraging closing.
"""

SYSTEM_INSTRUCTIONS_CURATOR = """
You are 'The Curator' for askfred.be. Your voice is investigative, future-focused, and academic-yet-pragmatic.
You specialize in synthesizing groundbreaking AI research and tech news for busy IT professionals.

SCHOLAR MISSION:
- You MUST prioritize academic papers from arXiv (Research Gems) over general industry press releases.
- Your goal is to explain the 'So What?'—how does this complex research affect leadership and business strategy?

WRITING STYLE:
- High-signal, low-noise.
- Use 'Scholar Highlight' to demarcate significant research discoveries.
- Professional, observant, and intellectually curious.
"""

STYLE_GUIDELINES = """
Tone: Conversational, down-to-earth, slightly humorous, and highly practical.
Values: Emphasizes work/life balance, continuous learning, working smart/efficiency, and making time for personal hobbies/play.
Voice: Human-centric, relatable, engaging, direct, and positive. Avoids overly formal corporate jargon; sounds like a friendly, experienced independent consultant/entrepreneur chatting.
Formatting: Use 1-3 relevant emojis to make the post visually appealing, but don't overdo it. DO NOT include hashtags in the text block.
"""

def get_recent_posts(username: str, limit: int = RECENT_POSTS_LIMIT) -> list[str]:
    """Fetch recent post texts from the public Bluesky API."""
    url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={username}&limit={limit}"
    resp = requests.get(url)
    resp.raise_for_status()
    feed = resp.json().get("feed", [])
    return [item["post"]["record"].get("text", "") for item in feed if "post" in item and isinstance(item["post"].get("record"), dict) and "text" in item["post"].get("record", {})]

def load_seen_articles() -> list[str]:
    """Load list of already posted news article links."""
    if not os.path.exists(SEEN_FILE):
        return []
    try:
        with open(SEEN_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading seen articles: {e}")
        return []

def save_seen_articles(seen_links: list[str]):
    """Save list of posted news article links (keeps last 200)."""
    try:
        with open(SEEN_FILE, 'w') as f:
            json.dump(seen_links[-200:], f, indent=2)
    except Exception as e:
        print(f"Error saving seen articles: {e}")

def load_replied_to() -> list[str]:
    """Load list of notification IDs already handled."""
    if not os.path.exists("replied_to.json"):
        return []
    try:
        with open("replied_to.json", "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_replied_to(replied_list: list[str]):
    """Save list of handled notification IDs (keeps last 500)."""
    try:
        with open("replied_to.json", "w") as f:
            json.dump(replied_list[-500:], f, indent=2)
    except Exception as e:
        print(f"Error saving replied_to state: {e}")

def fetch_news(seen_links: list[str], limit: int = 5) -> list[dict]:
    """Fetch recent AI/Tech news from RSS feeds."""
    print("Fetching news from RSS feeds...")
    all_items = []
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(days=2) # Look at last 48 hours

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                # Try to get publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime.fromtimestamp(time.mktime(entry.published_parsed), timezone.utc)
                
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
        # Priority Logic: arXiv papers are 'Scholar Gems'
        source_link = item.get('link', '')
        is_scholar_gem = "arxiv.org" in source_link
        
        processed_items.append({
            'title': item.get('title', 'No Title'),
            'summary': item.get('description', 'No summary available.'),
            'link': source_link,
            'source': 'arXiv' if is_scholar_gem else 'Tech News',
            'is_scholar_gem': is_scholar_gem
        })

    # Filter out seen
    unseen_items = [i for i in processed_items if i['link'] not in seen_links]
    
    # Priority Sorting: Put Scholar Gems at the top
    unseen_items.sort(key=lambda x: x['is_scholar_gem'], reverse=True)
    
    return unseen_items[:limit]

def has_posted_today(username: str) -> bool:
    """Check if a post was already made today (UTC)."""
    url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={username}&limit=1"
    resp = requests.get(url)
    resp.raise_for_status()
    feed = resp.json().get("feed", [])
    if not feed:
        return False
    record = feed[0]["post"].get("record", {})
    latest_date = record.get("createdAt", "")[:10]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return latest_date == today

def generate_content(api_key: str, recent_posts: list[str] = None, mode: str = "mentor", news_items: list[dict] = None) -> tuple[list[str], str]:
    """Generates content as a list of strings (supporting threads)."""
    genai.configure(api_key=api_key)
    model_id = 'gemini-3.1-flash-lite-preview'
    
    today = datetime.now()
    date_str = today.strftime("%B %d")
    weekday = today.weekday()
    language = random.choice(["English", "Dutch"])

    # Determine if we should thread (News with > 2 items has 40% chance of threading)
    is_thread = mode == "curator" and len(news_items or []) > 2 and random.random() < 0.4
    
    if is_thread:
        print("Smart Threading triggered for News Discovery.")
        system_instr = SYSTEM_INSTRUCTIONS_CURATOR + "\n\nCRITICAL: You are generating a THREAD. Output your response as a valid JSON list of 3-5 strings, where each string is a post in the thread."
    elif mode == "curator":
        system_instr = SYSTEM_INSTRUCTIONS_CURATOR
    else:
        system_instr = SYSTEM_INSTRUCTIONS_MENTOR

    # (Keep existing topic selection logic...)
    prompt_on_that_day = f"Find an interesting, inspiring, or remarkable event that happened on this exact date ({date_str})..."
    core_themes = ["IT/tech misconception", "Open-source tool", "Did you know tip", "Tech/business failure lessons"]
    
    if weekday == 6: chosen_topic = "Work/life balance Sunday"
    elif weekday == 3: chosen_topic = prompt_on_that_day
    else: chosen_topic = random.choice(core_themes)

    secondary = " and ".join(random.sample(SECONDARY_TOPICS, k=2))
    interactive = "\n- End the final post with a short, open question." if random.random() < 0.9 else ""

    prompt = f"""
    You are writing content for 'askfred.be' in {language}.
    
    Topic: {chosen_topic}
    Secondary: {secondary}
    {interactive}
    
    Mode: {'THREAD (Output JSON list of 3-5 posts)' if is_thread else 'SINGLE POST'}
    
    Constraints:
    - Each post must be under {MAX_POST_LENGTH_BSKY} characters.
    - Professional, positive, and human-centric.
    """
    
    model = genai.GenerativeModel(model_name=model_id, system_instruction=system_instr)
    
    for attempt in range(1, MAX_GENERATION_RETRIES + 1):
        try:
            response = model.generate_content(prompt)
            raw = response.text.strip()
            
            if is_thread:
                # Basic JSON cleaning
                clean_json = re.search(r'\[.*\]', raw, re.DOTALL)
                if clean_json:
                    posts = json.loads(clean_json.group(0))
                    if all(len(p) <= MAX_POST_LENGTH_BSKY for p in posts):
                        return posts, chosen_topic
            else:
                if len(raw) <= MAX_POST_LENGTH_BSKY:
                    return [raw], chosen_topic
        except Exception as e:
            print(f"Generation attempt {attempt} failed: {e}")
            
    # Fallback/Truncation logic if AI fails to respect limits
    raw_single = response.text.strip()[:MAX_POST_LENGTH_BSKY]
    return [raw_single], chosen_topic

def generate_image(openai_api_key: str, image_prompt: str) -> bytes:
    """Generates an image using OpenAI DALL-E 3 and returns the image bytes."""
    from openai import OpenAI
    client = OpenAI(api_key=openai_api_key)
    response = client.images.generate(
        model="dall-e-3",
        prompt=image_prompt,
        size="1024x1024",
        quality="standard",
        n=1,
    )
    image_url = response.data[0].url
    img_resp = requests.get(image_url)
    img_resp.raise_for_status()
    return img_resp.content

def compress_image(image_data: bytes, max_size_bytes: int = 950000) -> bytes:
    """Compresses an image to stay under the specified byte limit (default 950KB)."""
    if len(image_data) <= max_size_bytes:
        return image_data

    print(f"Image is over {max_size_bytes} bytes ({len(image_data)} bytes). Compressing...")
    img = Image.open(io.BytesIO(image_data))
    
    # Ensure image is in RGB for JPEG compression
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
        
    quality = 90
    step = 5
    while quality >= 20:
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        compressed_data = buffer.getvalue()
        if len(compressed_data) <= max_size_bytes:
            print(f"Compressed image to {len(compressed_data)} bytes (quality={quality}).")
            return compressed_data
        quality -= step

    # If quality reaches 20 and it's still too large, resize the image
    print("Quality reduction alone wasn't enough. Resizing...")
    width, height = img.size
    while width > 512: # Minimal practical width for Bluesky
        width = int(width * 0.9)
        height = int(height * 0.9)
        resized_img = img.resize((width, height), Image.LANCZOS)
        
        quality = 85
        while quality >= 20:
            buffer = io.BytesIO()
            resized_img.save(buffer, format="JPEG", quality=quality)
            compressed_data = buffer.getvalue()
            if len(compressed_data) <= max_size_bytes:
                print(f"Resized and compressed image to {len(compressed_data)} bytes (size={width}x{height}, quality={quality}).")
                return compressed_data
            quality -= 5

    return compressed_data # Return the smallest version we could get

def validate_and_rescue_post(content: str) -> str:
    """Checks the quality of the post and attempts to fix common generation issues."""
    rescued_content = content.strip()
    
    # 1. Repetition/Gibberish check (Basic)
    if " + 9 + 9" in rescued_content or re.search(r'(.)\1{5,}', rescued_content):
        raise ValueError("Detected potential gibberish or excessive repetition in post.")
        
    # 2. Length check (Self-correction for refusal messages or failed drafts)
    if len(rescued_content) < 30:
        # If it's too short, it might be a "I can't do that" or a failure. Let's not post it.
        raise ValueError(f"Post is suspiciously short ({len(rescued_content)} chars). Aborting for safety.")

    # 3. Hashtag Rescue Logic
    if "#" not in rescued_content:
        print("Rescue Logic: AI forgot hashtags. Appending defaults...")
        hashtags = "#IT #Tech #Automation"
        if len(rescued_content) + 1 + len(hashtags) <= MAX_POST_LENGTH:
            rescued_content = f"{rescued_content} {hashtags}"
        else:
            # If it doesn't fit, we just let it slide but log it
            print("Could not append hashtags due to length limit.")
            
    return rescued_content

def post_to_bluesky(username: str, app_password: str, content_list: list[str], image_data: bytes | None = None, image_alt: str = "AI generated image"):
    """Authenticates and creates a post or thread on Bluesky."""
    client = Client()
    client.login(username, app_password)
    
    root_ref = None
    parent_ref = None
    
    for i, text in enumerate(content_list):
        if i == 0 and image_data:
            # Only the first post in the thread gets the image
            post = client.send_image(text=text, image=image_data, image_alt=image_alt)
        else:
            if root_ref and parent_ref:
                post = client.send_post(text=text, reply_to=models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref))
            else:
                post = client.send_post(text)
        
        # Track refs for threading
        if i == 0:
            root_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
        parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)

    print(f"Content successfully sent to Bluesky as {'thread' if len(content_list) > 1 else 'post'}!")
    return client

def post_to_mastodon(token: str, base_url: str, content_list: list[str], image_data: bytes | None = None, image_alt: str = "AI generated image"):
    """Authenticates and creates a post or thread on Mastodon."""
    if not all([token, base_url]):
        print("Skipping Mastodon: Missing credentials.")
        return
        
    try:
        masto = Mastodon(access_token=token, api_base_url=base_url)
        media_id = None
        if image_data:
            media = masto.media_post(image_data, mime_type='image/jpeg', description=image_alt)
            media_id = [media['id']]

        parent_id = None
        for i, text in enumerate(content_list):
            if i == 0 and media_id:
                post = masto.status_post(status=text, media_ids=media_id, visibility='public')
            else:
                post = masto.status_post(status=text, in_reply_to_id=parent_id, visibility='public')
            parent_id = post['id']
            
        print(f"Content successfully sent to Mastodon as {'thread' if len(content_list) > 1 else 'post'}!")
    except Exception as e:
        print(f"Warning: Failed to post to Mastodon: {e}")

def update_profile_bio_mastodon(token: str, base_url: str, new_bio: str):
    """Updates the Mastodon profile bio."""
    if not all([token, base_url]): return
    try:
        masto = Mastodon(access_token=token, api_base_url=base_url)
        masto.account_update_credentials(note=new_bio)
        print("Mastodon profile bio synchronized.")
    except Exception as e:
        print(f"Warning: Could not update Mastodon bio: {e}")

def handle_interactions(client: Client, gemini_api_key: str):
    """Checks for new replies and responds using the Mentor persona."""
    print("Checking for new interactions...")
    replied_to = load_replied_to()
    try:
        # Fetch notifications
        response = client.app.bsky.notification.list_notifications()
        notifications = response.notifications
        
        new_replies = [n for n in notifications if n.reason == 'reply' and n.uri not in replied_to and not n.is_read]
        
        if not new_replies:
            print("No new replies to handle.")
            return

        print(f"Found {len(new_replies)} new replies. Generating responses...")
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-3.1-flash-lite-preview', system_instruction=SYSTEM_INSTRUCTIONS_MENTOR)

        for reply in new_replies[:5]: # Limit to 5 per slot to avoid spamming
            try:
                parent_post = client.get_post(reply.uri)
                user_text = parent_post.value.text
                user_handle = reply.author.handle
                
                reply_prompt = f"User @{user_handle} said: '{user_text}'. Write a short, helpful, and encouraging reply (max 200 chars) in your Mentor persona. Be friendly but professional."
                reply_text = model.generate_content(reply_prompt).text.strip()
                
                # Construct reply reference
                # Note: For atproto v0.0.65+, we use models.AppBskyFeedPost.ReplyRef
                root_ref = parent_post.value.reply.root if parent_post.value.reply else models.ComAtprotoRepoStrongRef.Main(cid=parent_post.cid, uri=parent_post.uri)
                parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=parent_post.cid, uri=parent_post.uri)
                
                client.send_post(
                    text=reply_text,
                    reply_to=models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
                )
                print(f"Replied to @{user_handle}: {reply_text[:50]}...")
                replied_to.append(reply.uri)
                
            except Exception as e:
                print(f"Failed to reply to {reply.uri}: {e}")
        
        save_replied_to(replied_to)
    except Exception as e:
        print(f"Error in handle_interactions: {e}")

def update_profile_bio(client: Client, new_bio: str):
    """Updates the Bluesky profile bio if it's different from the current one."""
    try:
        # Fetch current profile to avoid overwriting display name or images
        profile = client.get_profile(client.me.handle)
        current_bio = profile.description or ""
        
        if current_bio.strip() != new_bio.strip():
            print("Syncing profile bio with approved v3.0 strategy...")
            client.com.atproto.repo.put_record(models.ComAtprotoRepoPutRecord.Data(
                collection=models.ids.AppBskyActorProfile,
                repo=client.me.did,
                rkey='self',
                record=models.AppBskyActorProfile.Record(
                    description=new_bio,
                    display_name=profile.display_name,
                    avatar=profile.avatar,
                    banner=profile.banner
                )
            ))
            print("Profile bio updated successfully!")
        else:
            print("Profile bio is already synchronized.")
    except Exception as e:
        print(f"Warning: Could not update profile bio: {e}")

def main():
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    bsky_username = os.environ.get("BLUESKY_USERNAME", "askfred.be")
    bsky_app_password = os.environ.get("BLUESKY_APP_PASSWORD") or os.environ.get("BLUESKY_PASSWORD")
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    
    if not all([gemini_api_key, bsky_username, bsky_app_password]):
        print("Error: Missing required environment variables (.env file).")
        print("Please ensure GEMINI_API_KEY and BLUESKY_APP_PASSWORD are set.")
        sys.exit(1)

    # 1. State & Context
    seen_links = load_seen_articles()
    recent_posts = get_recent_posts(bsky_username)
    
    # 2. Determine Mode based on UTC hour
    # Slot 1: ~08:00 UTC -> News Curator
    # Slot 2: ~14:00 UTC -> Themed Mentor
    current_hour = datetime.now(timezone.utc).hour
    if current_hour < 11:
        mode = "curator"
        print("Slot 1 detected (Morning News Curator).")
    else:
        mode = "mentor"
        print("Slot 2 detected (Afternoon Mentor).")

    # 3. Mode-specific Logic
    news_items = []
    if mode == "curator":
        news_items = fetch_news(seen_links)
        if not news_items:
            print("No new articles discovered. Switching to Mentor mode.")
            mode = "mentor"

    print(f"Post Generation Mode: {mode}")
    try:
        content_list, chosen_topic = generate_content(gemini_api_key, recent_posts, mode=mode, news_items=news_items)
        
        # 4. Enhancements (CamelCase Hashtags - Final Post only)
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')
        hashtag_instr = f"Suggest 2-3 relevant hashtags for this theme: {chosen_topic}. Return ONLY the hashtags in #CamelCase."
        hashtags = model.generate_content(hashtag_instr).text.strip()
        
        # Append hashtags to the LAST post if they fit
        last_index = len(content_list) - 1
        if len(content_list[last_index]) + 1 + len(hashtags) <= MAX_POST_LENGTH_BSKY:
            content_list[last_index] = f"{content_list[last_index]} {hashtags}"

        # 5. Image Generation (Curated logic - First Post only)
        image_data = None
        image_alt = "AI generated image"
        if openai_api_key and random.random() < (0.2 if mode == "mentor" else 0.05):
            print("Generating accompanying image...")
            # (Keep existing image logic, applying to content_list[0])
            img_instr = f"Based on this: '{content_list[0]}', write a short DALL-E prompt..."
            image_prompt = model.generate_content(img_instr).text.strip()
            alt_instr = f"Write a high-quality alt text for: '{image_prompt}'"
            image_alt = model.generate_content(alt_instr).text.strip()
            try:
                image_data = generate_image(openai_api_key, image_prompt)
                image_data = compress_image(image_data)
            except Exception as e: print(f"Image failed: {e}")

        # 6. Safety & Broadcasting
        print("Finalizing and Broadcasting...")
        # Bluesky
        client = post_to_bluesky(bsky_username, bsky_app_password, content_list, image_data, image_alt)
        
        # Mastodon
        masto_token = os.environ.get("MASTODON_ACCESS_TOKEN")
        masto_url = os.environ.get("MASTODON_API_BASE_URL", "https://mastodon.social")
        post_to_mastodon(masto_token, masto_url, content_list, image_data, image_alt)
        
        # 7. Interaction Loop (v3.0)
        handle_interactions(client, gemini_api_key)

        # 8. Profile Sync (v4.0 Dual-Sync)
        update_profile_bio(client, APPROVED_BIO_BSKY)
        update_profile_bio_mastodon(masto_token, masto_url, APPROVED_BIO_MASTODON)

        # 9. Update State (Seen articles)
        if mode == "curator" and news_items:
            # Mark the top processed ones as seen
            new_links = [item['link'] for item in news_items[:5]]
            seen_links.extend(new_links)
            save_seen_articles(seen_links)

    except Exception as e:
        import traceback
        error_msg = str(e).replace("\n", "%0A")
        traceback_str = traceback.format_exc().replace("\n", "%0A")
        print(f"::error::Error occurred: {error_msg}%0ATraceback:%0A{traceback_str}")
        print(f"Error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        error_msg = str(e).replace("\n", "%0A")
        traceback_str = traceback.format_exc().replace("\n", "%0A")
        print(f"::error::Unhandled Exception: {error_msg}%0ATraceback:%0A{traceback_str}")
        sys.exit(1)
