import os
import random
from datetime import datetime
import google.generativeai as genai
from atproto import Client
from dotenv import load_dotenv

load_dotenv()

STYLE_GUIDELINES = """
Tone: Conversational, down-to-earth, slightly humorous, and highly practical.
Values: Emphasizes work/life balance, continuous learning, working smart/efficiency, and making time for personal hobbies/play.
Voice: Human-centric, relatable, engaging, direct, and positive. Avoids overly formal corporate jargon; sounds like a friendly, experienced independent consultant/entrepreneur chatting.
"""

def generate_post(api_key: str) -> str:
    """Generates a professional, positive post."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    today = datetime.now()
    date_str = today.strftime("%B %d") # e.g., March 20
    
    prompt_on_that_day = f"""
    Find an interesting, inspiring, or remarkable event that happened on this exact date ({date_str}) in a specific year in the past.
    It can be about technology, science, culture, arts, or general history.
    Start your post exactly with: "On that day in YYYY" (replace YYYY with the actual year).
    """
    
    prompt_tip = """
    Share a "Nice to know" or positive tip related to IT, business, productivity, or just a good life lesson.
    """
    
    chosen_topic = random.choice([prompt_on_that_day, prompt_on_that_day, prompt_tip]) # Higher chance for historical fact
    
    prompt = f"""
    You are writing a Bluesky post for the account 'askfred.be'.
    
    Topic instructions:
    {chosen_topic}
    
    Guidelines:
    - Keep it professional, positive, interesting, and helpful.
    - It should make the reader feel good.
    - Mimic the following personal writing style and tone:
    {STYLE_GUIDELINES}
    
    Write the exact final text for the post. Do not add any internal thoughts or surrounding quotes.
    The post must be strictly under 300 characters.
    """
    
    response = model.generate_content(prompt)
    return response.text.strip()

def post_to_bluesky(username: str, password: str, text: str):
    """Authenticates and creates a post on Bluesky."""
    client = Client()
    client.login(username, password)
    client.send_post(text)
    print("Post successfully sent to Bluesky!")

def main():
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    bsky_username = os.environ.get("BLUESKY_USERNAME", "askfred.be")
    bsky_password = os.environ.get("BLUESKY_PASSWORD")
    
    if not all([gemini_api_key, bsky_username, bsky_password]):
        print("Error: Missing required environment variables (.env file).")
        print("Please ensure GEMINI_API_KEY and BLUESKY_PASSWORD are set.")
        return

    print("Generating post content...")
    try:
        content = generate_post(gemini_api_key)
        print(f"\nGenerated text ({len(content)} chars):\n---\n{content}\n---\n")
        
        print("Posting to Bluesky...")
        post_to_bluesky(bsky_username, bsky_password, content)
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == "__main__":
    main()
