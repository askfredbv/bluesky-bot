import os
import random
from datetime import datetime
import google.generativeai as genai
from atproto import Client
from dotenv import load_dotenv
import requests

load_dotenv()

STYLE_GUIDELINES = """
Tone: Conversational, down-to-earth, slightly humorous, and highly practical.
Values: Emphasizes work/life balance, continuous learning, working smart/efficiency, and making time for personal hobbies/play.
Voice: Human-centric, relatable, engaging, direct, and positive. Avoids overly formal corporate jargon; sounds like a friendly, experienced independent consultant/entrepreneur chatting.
"""

def generate_post(api_key: str) -> tuple[str, str]:
    """Generates a professional, positive post."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    today = datetime.now()
    date_str = today.strftime("%B %d") # e.g., March 20
    
    language = random.choice(["English", "Dutch"])
    
    prompt_on_that_day = f"""
    Find an interesting, inspiring, or remarkable event that happened on this exact date ({date_str}) in a specific year in the past.
    It can be about technology, science, culture, arts, or general history.
    If writing in English, start your post exactly with: "On that day in YYYY".
    If writing in Dutch, start your post exactly with: "Op deze dag in YYYY".
    Replace YYYY with the actual year in both cases.
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
    - Write the entire post naturally in {language}.
    - Keep it professional, positive, interesting, and helpful.
    - It should make the reader feel good.
    - Mimic the following personal writing style and tone:
    {STYLE_GUIDELINES}
    
    Write the exact final text for the post. Do not add any internal thoughts or surrounding quotes.
    The post must be strictly under 300 characters.
    """
    
    response = model.generate_content(prompt)
    return response.text.strip(), chosen_topic

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
        return

    print("Generating post content...")
    try:
        content, chosen_topic = generate_post(gemini_api_key)
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

if __name__ == "__main__":
    main()
