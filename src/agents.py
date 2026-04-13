import json
import random
import asyncio
import re
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timezone
import google.generativeai as genai
from src.config import (
    SYSTEM_INSTRUCTIONS_MENTOR, SYSTEM_INSTRUCTIONS_CURATOR,
    STYLE_GUIDELINES, SECONDARY_TOPICS, MAX_POST_LENGTH_BSKY,
    REPLY_CAP_PER_RUN
)
from src.utils import load_replied_to, save_replied_to
from src.logger import SafeLogger

def _sanitize_mention(text: str) -> str:
    """Strip potential prompt injection characters and normalize."""
    # Basic cleaning
    clean = text.replace('\n', ' ').strip()
    # Mask common injection keywords (Fortress v4.4)
    clean = re.sub(r"(ignore|previous|instruction|system|prompt)", "[redacted]", clean, flags=re.IGNORECASE)
    return clean[:500]

def _sync_generate(api_key: str, full_prompt: str) -> str:
    """Helper for synchronous Gemini call."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash-lite-preview-0924')
    response = model.generate_content(full_prompt)
    return response.text

async def generate_content(api_key: str, recent_posts: List[str], mode: str = "mentor", news_items: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[str], str]:
    """Generates content asynchronously using Gemini."""
    if mode == "curator" and news_items:
        context = "\n".join([f"- {i['title']}: {i['summary']} ({i['link']})" for i in news_items])
        topic = news_items[0]['title']
        prompt = f"{SYSTEM_INSTRUCTIONS_CURATOR}\n{STYLE_GUIDELINES}\n\nTOP RESEARCH/NEWS:\n{context}\n\nTask: Synthesize a professional thread (3-5 posts)."
    else:
        topic = random.choice(SECONDARY_TOPICS)
        prompt = f"{SYSTEM_INSTRUCTIONS_MENTOR}\n{STYLE_GUIDELINES}\n\nTHEME: {topic}\n\nTask: Share a human-centric IT mentorship wisdom thread (3-5 posts)."

    full_prompt = f"{prompt}\n\nRECENT TOPICS TO AVOID: {', '.join(recent_posts[:5])}\nFormat your response as a JSON list of strings."
    
    response_text = await asyncio.to_thread(_sync_generate, api_key, full_prompt)
    
    try:
        clean_text = response_text.replace('```json', '').replace('```', '').strip()
        content_list = json.loads(clean_text)
        return content_list, topic
    except Exception as e:
        SafeLogger.error("Failed to parse AI response", e)
        return [response_text[:MAX_POST_LENGTH_BSKY]], topic

async def handle_interactions(client: Any, bsky_username: str, api_key: str) -> None:
    """Checks and handles interactions asynchronously with Fortress capping."""
    print("Checking for interactions and applying Fortress caps...")
    replied_to = load_replied_to()
    
    try:
        notifications = await client.app.bsky.notification.list_notifications()
        mentions = [n for n in notifications.notifications if n.reason == 'mention' and not n.is_read]
        
        if not mentions:
            print("No new mentions.")
            return

        # Fortress v4.4: Apply Reply Cap
        active_mentions = mentions[:REPLY_CAP_PER_RUN]
        if len(mentions) > REPLY_CAP_PER_RUN:
            SafeLogger.warn(f"Metion cap reached! Processing first {REPLY_CAP_PER_RUN} out of {len(mentions)} notifications.")

        for mention in active_mentions:
            if mention.uri in replied_to: continue
            
            # Fortress v4.4: Sanitize Input
            original_text = mention.record.text
            sanitized_text = _sanitize_mention(original_text)
            
            print(f"Replying to {mention.author.handle}...")
            reply_instr = f"{SYSTEM_INSTRUCTIONS_MENTOR}\n\nUser Question: '{sanitized_text}'. Write a friendly, helpful reply under 250 chars."
            
            ai_reply = await asyncio.to_thread(_sync_generate, api_key, reply_instr)
            ai_reply = ai_reply.strip()
            
            parent_ref = {'cid': mention.cid, 'uri': mention.uri}
            root_ref = parent_ref
            
            await client.send_post(
                text=ai_reply,
                reply_to={'parent': parent_ref, 'root': root_ref}
            )
            replied_to.append(mention.uri)
            
        save_replied_to(replied_to)
    except Exception as e:
        SafeLogger.error("Interaction error during Fortress handling", e)
