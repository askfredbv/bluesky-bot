import json
import random
import asyncio
import re
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, timezone
from google import genai
from src.config import (
    SYSTEM_INSTRUCTIONS_MENTOR, SYSTEM_INSTRUCTIONS_CURATOR,
    MAX_POST_LENGTH_BSKY, REPLY_CAP_PER_RUN, SECONDARY_TOPICS
)
from src.utils import update_replied_to
from src.logger import SafeLogger

def _sanitize_mention(text: str) -> str:
    """Strip potential prompt injection characters and normalize (Fortress v4.4)."""
    clean = text.replace('\n', ' ').strip()
    clean = re.sub(r"(ignore|previous|instruction|system|prompt)", "[redacted]", clean, flags=re.IGNORECASE)
    return clean[:500]

def get_temporal_context() -> Dict[str, str]:
    """Returns day-aware contextual themes (Sage v4.5)."""
    now = datetime.now(timezone.utc)
    day = now.strftime("%A")
    hour = now.hour
    
    context = {"day": day}
    
    if day == "Monday":
        context["theme"] = "Setting the weekly strategy and forward-looking momentum."
    elif day == "Friday":
        context["theme"] = "Capping off the week with synthesis and reflective analysis."
    elif day in ["Saturday", "Sunday"]:
        context["theme"] = "Weekend high-level vision and community reflection."
    else:
        context["theme"] = "Mid-week technical deep-dives and progress tracking."
        
    context["session"] = "Morning Intelligence Briefing" if hour < 12 else "Afternoon Mentor Deep-Dive"
    return context

def validate_summary(text: str) -> Tuple[bool, str]:
    """Heuristic validation of AI output quality (Rescue v4.5)."""
    if not text: return False, "Empty output"
    if len(text) < 60: return False, "Too short for high-signal insight"
    if re.search(r'(.)\1{4,}', text): return False, "Detected repetitive pattern/gibberish"
    if "#" not in text: return False, "Missing thematic hashtags"
    return True, "Success"

def _sync_generate(api_key: str, full_prompt: str) -> str:
    """Helper for synchronous Gemini call."""
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=full_prompt,
    )
    return response.text

async def generate_content(api_key: str, recent_posts: List[str], mode: str = "mentor", news_items: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[str], str]:
    """Generates content asynchronously with Rescue logic and Temporal Context."""
    temporal = get_temporal_context()
    
    if mode == "curator" and news_items:
        # FIX: utils.py stores the field as 'description', not 'summary'
        news_text = "\n".join([f"- {i['title']}: {i.get('description', '')} ({i['link']})" for i in news_items])
        topic = news_items[0]['title']
        instr = SYSTEM_INSTRUCTIONS_CURATOR
        task = f"Context: {temporal['day']} {temporal['session']}.\nTheme: {temporal['theme']}\n\nRESEARCH:\n{news_text}\n\nTask: Synthesize a professional thread."
    elif mode == "strategist":
        topic = random.choice(SECONDARY_TOPICS)
        instr = SYSTEM_INSTRUCTIONS_MENTOR
        task = f"Context: {temporal['day']} {temporal['session']}.\nTheme: {temporal['theme']}\n\nSTRATEGIC DISCUSSION: {topic}\n\nTask: Share deep, abstract mentorship and philosophical insight purely on this topic."
    else:
        topic = random.choice(["Career", "Automation", "Work-Life Balance", "Learning"])
        instr = SYSTEM_INSTRUCTIONS_MENTOR
        task = f"Context: {temporal['day']} {temporal['session']}.\nTheme: {temporal['theme']}\n\nTOPIC: {topic}\n\nTask: Share mentorship wisdom."

    full_prompt = f"{instr}\n\n{task}\n\nRECENTLY SAID (AVOID): {', '.join(recent_posts[:3])}\nFormat as a JSON list of strings (3-5 posts)."
    
    # Implementation of Rescue Pipeline
    for attempt in range(2):
        response_text = await asyncio.to_thread(_sync_generate, api_key, full_prompt)
        try:
            clean_text = response_text.replace('```json', '').replace('```', '').strip()
            content_list = json.loads(clean_text)
            
            # Sub-validation of the primary post
            is_valid, reason = validate_summary(content_list[0])
            if is_valid: return content_list, topic
            
            # Repair Attempt: Force hashtags if missing but length is good
            if reason == "Missing thematic hashtags":
                print("Rescue Logic: Repairing missing hashtags...")
                content_list[0] += f" #{topic.replace(' ', '')} #TechUpdate"
                return content_list, topic
                
        except Exception:
            pass
        SafeLogger.warn(f"Output validation failed (Attempt {attempt+1}). Retrying...")

    return [response_text[:MAX_POST_LENGTH_BSKY]], topic

async def handle_interactions(client: Any, bsky_username: str, api_key: str) -> None:
    """Checks and handles interactions asynchronously (Fortress v4.4)."""
    print("Checking for interactions...")
    try:
        replied_to = set(update_replied_to(lambda current: current))
        notifications = await client.app.bsky.notification.list_notifications()
        mentions = [n for n in notifications.notifications if n.reason == 'mention' and not n.is_read]
        
        if not mentions: return

        active_mentions = mentions[:REPLY_CAP_PER_RUN]
        for mention in active_mentions:
            if mention.uri in replied_to: continue
            
            sanitized_text = _sanitize_mention(mention.record.text)
            print(f"Replying to {mention.author.handle}...")
            
            reply_instr = f"{SYSTEM_INSTRUCTIONS_MENTOR}\n\nQuestion: '{sanitized_text}'. Write a helpful, friendly reply under 250 chars."
            ai_reply = await asyncio.to_thread(_sync_generate, api_key, reply_instr)
            
            await client.send_post(
                text=ai_reply.strip()[:290],
                reply_to={'parent': {'cid': mention.cid, 'uri': mention.uri}, 'root': {'cid': mention.cid, 'uri': mention.uri}}
            )
            replied_to.add(mention.uri)

        update_replied_to(lambda _: list(replied_to))
    except Exception as e:
        SafeLogger.error("Interaction error", e)
