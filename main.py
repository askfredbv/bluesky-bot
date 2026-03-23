import os
import sys
import random
from datetime import datetime, timezone
import google.generativeai as genai
from atproto import Client
from dotenv import load_dotenv
import requests

load_dotenv()

MAX_POST_LENGTH = 300
MAX_GENERATION_RETRIES = 3
RECENT_POSTS_LIMIT = 7

STYLE_GUIDELINES = """
Tone: Conversational, down-to-earth, slightly humorous, and highly practical.
Values: Emphasizes work/life balance, continuous learning, working smart/efficiency, and making time for personal hobbies/play.
Voice: Human-centric, relatable, engaging, direct, and positive. Avoids overly formal corporate jargon; sounds like a friendly, experienced independent consultant/entrepreneur chatting.
"""

def get_recent_posts(username: str, limit: int = RECENT_POSTS_LIMIT) -> list[str]:
    """Fetch recent post texts from the public Bluesky API."""
    url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={username}&limit={limit}"
    resp = requests.get(url)
    resp.raise_for_status()
    feed = resp.json().get("feed", [])
    return [item["post"]["record"]["text"] for item in feed if "post" in item and "record" in item["post"]]

def has_posted_today(username: str) -> bool:
    """Check if a post was already made today (UTC)."""
    url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={username}&limit=1"
    resp = requests.get(url)
    resp.raise_for_status()
    feed = resp.json().get("feed", [])
    if not feed:
        return False
    latest_date = feed[0]["post"]["record"]["createdAt"][:10]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return latest_date == today

def generate_post(api_key: str, recent_posts: list[str] | None = None) -> tuple[str, str]:
    """Generates a professional, positive post."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    today = datetime.now()
    date_str = today.strftime("%B %d") # e.g., March 20
    weekday = today.weekday() # 0 = Monday, 6 = Sunday
    
    language = random.choice(["English", "Dutch"])
    
    prompt_on_that_day = f"""
    Find an interesting, inspiring, or remarkable event that happened on this exact date ({date_str}) in a specific year in the past.
    It can be about technology, science, culture, arts, or general history.
    If writing in English, start your post exactly with: "On that day in YYYY".
    If writing in Dutch, start your post exactly with: "Op deze dag in YYYY".
    Replace YYYY with the actual year in both cases.
    """
    
    themes = {
        0: "Motivational Monday: Share a brief, positive piece of IT/business advice or a good life lesson to start the week strong.",
        1: "Tool Tuesday: Highlight a fantastic, highly useful open-source tool or software.",
        2: "Positive Wednesday: Share a genuinely uplifting piece of historical or recent technology/science news.",
        3: prompt_on_that_day,  # Throwback Thursday
        4: "Failure Friday: Briefly discuss a famous tech or business failure, and the positive lesson learned from it.",
        5: "Shoutout Saturday: Write a positive shoutout praising the creators or maintainers of a well-known open-source project.",
        6: "Sunday Reset: Share a tip about work/life balance, disconnecting, or personal hobbies for busy IT professionals."
    }
    
    chosen_topic = themes.get(weekday)
    
    # Idea 1: Interactive Question (50% chance)
    interactive_prompt = ""
    if random.random() < 0.5:
        interactive_prompt = "\n    - End the post with a short, engaging question related to the topic to encourage readers to reply and share their experiences."
    
    prompt = f"""
    You are writing a Bluesky post for the account 'askfred.be'.
    
    Topic instructions:
    {chosen_topic}
    
    Guidelines:
    - Write the entire post naturally in {language}.
    - Keep it professional, positive, interesting, and helpful.
    - It should make the reader feel good.
    - Mimic the following personal writing style and tone:
    {STYLE_GUIDELINES}{interactive_prompt}
    
    Write the exact final text for the post. Do not add any internal thoughts or surrounding quotes.
    The post must be strictly under {MAX_POST_LENGTH} characters.
    """
    
    # Add recent posts context to avoid content repetition
    if recent_posts:
        recent_list = "\n".join(f"- {p}" for p in recent_posts)
        prompt += f"\n    IMPORTANT: Do NOT repeat or closely paraphrase any of these recent posts:\n{recent_list}\n"
    
    # Retry if Gemini generates text that exceeds the character limit
    for attempt in range(1, MAX_GENERATION_RETRIES + 1):
        response = model.generate_content(prompt)
        content = response.text.strip()
        if len(content) <= MAX_POST_LENGTH:
            return content, chosen_topic
        print(f"Attempt {attempt}/{MAX_GENERATION_RETRIES}: Generated text is {len(content)} chars (limit {MAX_POST_LENGTH}). Retrying...")
    
    raise ValueError(f"Failed to generate a post under {MAX_POST_LENGTH} characters after {MAX_GENERATION_RETRIES} attempts. Last attempt was {len(content)} chars.")

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

def post_to_bluesky(username: str, password: str, text: str, image_data: bytes | None = None):
    """Authenticates and creates a post on Bluesky."""
    client = Client()
    client.login(username, password)
    if image_data:
        client.send_image(text=text, image=image_data, image_alt="AI generated image")
    else:
        client.send_post(text)
    print("Post successfully sent to Bluesky!")

def main():
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    bsky_username = os.environ.get("BLUESKY_USERNAME", "askfred.be")
    bsky_password = os.environ.get("BLUESKY_PASSWORD")
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    
    if not all([gemini_api_key, bsky_username, bsky_password]):
        print("Error: Missing required environment variables (.env file).")
        print("Please ensure GEMINI_API_KEY and BLUESKY_PASSWORD are set.")
        sys.exit(1)

    # Check if we already posted today
    print("Checking for existing post today...")
    if has_posted_today(bsky_username):
        print("Already posted today, skipping.")
        return

    # Fetch recent posts for content repetition prevention
    print("Fetching recent posts for context...")
    recent_posts = get_recent_posts(bsky_username)
    print(f"Found {len(recent_posts)} recent posts to use as context.")

    print("Generating post content...")
    try:
        content, chosen_topic = generate_post(gemini_api_key, recent_posts)
        print(f"\nGenerated text ({len(content)} chars):\n---\n{content}\n---\n")
        
        image_data = None
        if openai_api_key and random.random() < 0.2:
            print("Decided to generate an accompanying image. Drafting prompt...")
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')
            img_instruction = f"Based on this topic: '{chosen_topic}', write a short, highly descriptive English prompt for an AI image generator like DALL-E to create a beautiful, professional accompaniment image. No wrapping text."
            img_prompt_req = model.generate_content(img_instruction)
            image_prompt = img_prompt_req.text.strip()
            
            print(f"Image prompt: {image_prompt}\nGenerating image...")
            image_data = generate_image(openai_api_key, image_prompt)
            print("Image generated and downloaded successfully.")

        print("Posting to Bluesky...")
        post_to_bluesky(bsky_username, bsky_password, content, image_data)
    except Exception as e:
        print(f"Error occurred: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

