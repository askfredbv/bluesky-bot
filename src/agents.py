import random
from datetime import datetime, timezone
import google.generativeai as genai
from src.config import (
    SYSTEM_INSTRUCTIONS_MENTOR, SYSTEM_INSTRUCTIONS_CURATOR,
    STYLE_GUIDELINES, SECONDARY_TOPICS, MAX_POST_LENGTH_BSKY
)
from src.utils import load_replied_to, save_replied_to

def generate_content(api_key, recent_posts, mode="mentor", news_items=None):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')
    
    if mode == "curator" and news_items:
        context = "\n".join([f"- {i['title']}: {i['summary']} ({i['link']})" for i in news_items])
        topic = news_items[0]['title'] # Use primary scholar gem as topic
        prompt = f"{SYSTEM_INSTRUCTIONS_CURATOR}\n{STYLE_GUIDELINES}\n\nTOP RESEARCH/NEWS:\n{context}\n\nTask: Synthesize a professional thread (3-5 posts)."
    else:
        topic = random.choice(SECONDARY_TOPICS)
        prompt = f"{SYSTEM_INSTRUCTIONS_MENTOR}\n{STYLE_GUIDELINES}\n\nTHEME: {topic}\n\nTask: Share a human-centric IT mentorship wisdom thread (3-5 posts)."

    # Avoid repeating recently discussed topics
    full_prompt = f"{prompt}\n\nRECENT TOPICS TO AVOID: {', '.join(recent_posts[:5])}\nFormat your response as a JSON list of strings."
    
    response = model.generate_content(full_prompt)
    try:
        import json
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        content_list = json.loads(clean_text)
        return content_list, topic
    except Exception as e:
        print(f"Failed to parse AI response: {e}")
        return [response.text[:MAX_POST_LENGTH_BSKY]], topic

def handle_interactions(client, bsky_username, api_key):
    print("Checking for interactions/replies...")
    replied_to = load_replied_to()
    
    try:
        notifications = client.app.bsky.notification.list_notifications()
        mentions = [n for n in notifications.notifications if n.reason == 'mention' and not n.is_read]
        
        if not mentions:
            print("No new mentions.")
            return

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')

        for mention in mentions:
            if mention.uri in replied_to: continue
            
            print(f"Replying to {mention.author.handle}...")
            # Simple reply logic
            reply_instr = f"{SYSTEM_INSTRUCTIONS_MENTOR}\n\nA user said: '{mention.record.text}'. Write a friendly, helpful reply under 250 chars."
            ai_reply = model.generate_content(reply_instr).text.strip()
            
            parent_ref = {'cid': mention.cid, 'uri': mention.uri}
            root_ref = parent_ref # Simplify for one-off replies
            
            # Using the client directly for simplicity in this utility
            client.send_post(
                text=ai_reply,
                reply_to={'parent': parent_ref, 'root': root_ref}
            )
            replied_to.append(mention.uri)
            
        save_replied_to(replied_to)
    except Exception as e:
        print(f"Interaction error: {e}")
