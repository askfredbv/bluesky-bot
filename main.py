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
from atproto import Client
from dotenv import load_dotenv
import requests
from PIL import Image

# Set network timeout and load environment
socket.setdefaulttimeout(15)
load_dotenv()

MAX_POST_LENGTH = 300
MAX_GENERATION_RETRIES = 3
RECENT_POSTS_LIMIT = 20
SEEN_FILE = "seen_articles.json"

RSS_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "https://export.arxiv.org/rss/cs.AI",
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

MAX_POST_LENGTH = 300
MAX_GENERATION_RETRIES = 3
RECENT_POSTS_LIMIT = 20

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
You are 'The Curator' for askfred.be. Your voice is sophisticated, insightful, and slightly ahead of the curve.
You don't just report news; you connect dots and provide a "Director's Cut" of the day's tech evolution.

CORE VALUES:
- Constructive Optimism: Every technical shift is a step toward a more capable future. 
- Technical Authority: Use precise terms (e.g., "latency," "throughput," "LLMs") but explain their weight.

WRITING STYLE:
- Fast-paced and insightful.
- Professional and analytical.
- Avoid generic "Latest news..." starts.
- Max 1 emoji. 

ARCHITECTURE:
1. THE CATALYST (Hook): Start with a specific piece of news.
2. THE SYNTHESIS (Impact): Explain why this matters in the larger narrative of tech.
3. THE INSIDER INSIGHT (The 'So What'): Provide a professional take on the long-term implication.
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

def fetch_news(seen_links: list[str]) -> list[dict]:
    """Fetch recent AI/Tech news from RSS feeds."""
    print("Fetching news from RSS feeds...")
    all_entries = []
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(days=2) # Look at last 48 hours

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                if entry.link in seen_links:
                    continue
                
                # Try to get publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime.fromtimestamp(time.mktime(entry.published_parsed), timezone.utc)
                
                if not pub_date or pub_date > lookback:
                    summary = entry.summary if hasattr(entry, 'summary') else ""
                    clean_summary = re.sub('<[^<]+?>', '', summary)[:300]
                    all_entries.append({
                        "title": entry.title,
                        "summary": clean_summary,
                        "link": entry.link,
                        "source": feed.feed.title if hasattr(feed.feed, 'title') else url
                    })
        except Exception as e:
            print(f"Error parsing feed {url}: {e}")
            
    return all_entries

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

def generate_post(api_key: str, recent_posts: list[str] | None = None, mode: str = "mentor", news_items: list[dict] | None = None) -> tuple[str, str]:
    """Generates a professional, positive post based on the requested mode."""
    genai.configure(api_key=api_key)
    # Upgrade to the latest 3.1 model
    model_id = 'gemini-3.1-flash-lite-preview'
    
    # Define timing variables early for use in all modes
    today = datetime.now()
    date_str = today.strftime("%B %d") # e.g., March 20
    weekday = today.weekday() # 0 = Monday, 6 = Sunday
    language = random.choice(["English", "Dutch"])

    if mode == "curator" and news_items:
        system_instr = SYSTEM_INSTRUCTIONS_CURATOR
        news_text = "\n".join([f"- {item['title']} (Source: {item['source']})\n  Context: {item['summary']}" for item in news_items[:5]])
        prompt = f"""
        Synthesize the following recent tech/AI updates into one high-engagement Bluesky post.
        Focus on the *meaning* behind the updates.
        
        News Data:
        {news_text}
        
        CRITICAL: The post must be strictly under {MAX_POST_LENGTH} characters.
        """
        chosen_topic = "News Curation"
    else:
        system_instr = SYSTEM_INSTRUCTIONS_MENTOR
        
    
    prompt_on_that_day = f"""
    Find an interesting, inspiring, or remarkable event that happened on this exact date ({date_str}) in a specific year in the past.
    It can be about technology, science, culture, arts, or general history.
    If writing in English, start your post exactly with: "On that day in YYYY".
    If writing in Dutch, start your post exactly with: "Op deze dag in YYYY".
    Replace YYYY with the actual year in both cases.
    """
    
    core_themes = [
        "Debunk a common misconception about IT/tech or share a counterintuitive productivity tip.",
        "Highlight a fantastic, highly useful open-source tool or software. What makes it special?",
        "Share a quick 'Did you know?' tip or a simple workflow improvement.",
        "Briefly discuss a famous tech or business failure, and the positive lesson learned from it.",
        "Write a positive shoutout praising the creators or maintainers of a well-known open-source project.",
        "Share a slightly humorous, relatable moment or 'gotcha' that IT professionals experience often.",
        "Give a concise, positive piece of career advice for junior developers or tech enthusiasts.",
        "Mention an underrated keyboard shortcut, terminal command, or tiny trick that saves time.",
        "Discuss the value of 'low-tech' hobbies for tech workers to prevent burnout.",
    ]
    
    if weekday == 6:  # Sunday
        chosen_topic = "Share a highly practical tip about disconnecting and work/life balance to recharge for the week ahead."
    elif weekday == 3: # Thursday
        chosen_topic = prompt_on_that_day
    else:
        chosen_topic = random.choice(core_themes)

    # Pick 1–2 secondary themes to enrich the post and prevent topic staleness
    secondary = random.sample(SECONDARY_TOPICS, k=random.randint(1, 2))
    secondary_str = " and ".join(secondary)

    # Idea 1: Interactive Question (90% chance to drive engagement)
    interactive_prompt = ""
    if random.random() < 0.9:
        interactive_prompt = "\n    - End the post with a short, open question like 'What is your go-to?' or 'Have you ever tried this?' to invite organic replies."

    prompt = f"""
    You are writing a Bluesky post for the account 'askfred.be'.

    Primary topic instructions:
    {chosen_topic}

    Secondary themes to naturally weave in (they should complement, not overshadow the primary topic):
    {secondary_str}

    Guidelines:
    - Write the entire post naturally in {language}.
    - Keep it professional, positive, interesting, and helpful.
    - NEVER use robotic phrases like "Happy Monday", "Tool Tuesday", or explicitly state the theme name. 
    {interactive_prompt}

    Write the exact final text for the post. Do not add any internal thoughts or surrounding quotes.
    CRITICAL: The post must be VERY concise. Aim for ~200-250 characters.
    """
    
    # Add recent posts context to avoid content repetition
    if recent_posts:
        recent_list = "\n".join(f"- {p}" for p in recent_posts)
        prompt += f"\n    IMPORTANT: Do NOT repeat or closely paraphrase any of these recent posts:\n{recent_list}\n"
    
    # Initialize model with system instruction
    model = genai.GenerativeModel(
        model_name=model_id,
        system_instruction=system_instr
    )

    # Retry if Gemini generates text that exceeds the character limit
    for attempt in range(1, MAX_GENERATION_RETRIES + 1):
        response = model.generate_content(prompt)
        content = response.text.strip()
        if len(content) <= MAX_POST_LENGTH:
            return content, chosen_topic
        print(f"Attempt {attempt}/{MAX_GENERATION_RETRIES}: Generated text is {len(content)} chars (limit {MAX_POST_LENGTH}). Retrying...")
    
    # If still too long, gracefully truncate to a sentence or word boundary
    print(f"Warning: Failed to naturally generate under {MAX_POST_LENGTH} chars. Forcibly truncating.")
    truncated = content[:MAX_POST_LENGTH]
    last_period = truncated.rfind(".")
    if last_period > len(truncated) * 0.5: # Only cut if we don't lose too much
        content = truncated[:last_period + 1]
    else:
        content = truncated.rsplit(" ", 1)[0] + "..."
    if len(content) > MAX_POST_LENGTH: 
        content = content[:MAX_POST_LENGTH]
    return content, chosen_topic

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

def post_to_bluesky(username: str, app_password: str, text: str, image_data: bytes | None = None, image_alt: str = "AI generated image"):
    """Authenticates and creates a post on Bluesky using an App Password."""
    client = Client()
    client.login(username, app_password)
    if image_data:
        client.send_image(text=text, image=image_data, image_alt=image_alt)
    else:
        client.send_post(text)
    print("Post successfully sent to Bluesky!")

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
        content, chosen_topic = generate_post(gemini_api_key, recent_posts, mode=mode, news_items=news_items)
        print(f"\nGenerated Content ({len(content)} chars):\n---\n{content}\n---\n")

        # 4. Enhancements (Hashtags)
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')
        hashtag_instr = f"Suggest 2-3 relevant hashtags for this Bluesky post. Return ONLY the hashtags:\n{content}"
        hashtags = model.generate_content(hashtag_instr).text.strip()
        if len(content) + 1 + len(hashtags) <= MAX_POST_LENGTH:
            content = f"{content} {hashtags}"
            print(f"Hashtags: {hashtags}")

        # 5. Image Generation (Curated logic)
        image_data = None
        image_alt = "AI generated image"
        image_chance = 0.2 if mode == "mentor" else 0.05
        
        if openai_api_key and random.random() < image_chance:
            print("Generating accompanying image...")
            img_instr = f"Based on this: '{content}', write a short DALL-E prompt for a professional tech-themed image."
            image_prompt = model.generate_content(img_instr).text.strip()
            image_alt = image_prompt
            try:
                image_data = generate_image(openai_api_key, image_prompt)
                image_data = compress_image(image_data)
            except Exception as e:
                print(f"Image failed: {e}")

        # 6. Safety Validation & Posting
        content = validate_and_rescue_post(content)
        print("Finalizing post...")
        post_to_bluesky(bsky_username, bsky_app_password, content, image_data, image_alt)
        
        # 7. Update State (Seen articles)
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
