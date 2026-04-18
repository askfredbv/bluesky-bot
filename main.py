import sys
import asyncio
import random
import uuid
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Internal Imports
from src.config import (
    THREAD_PAUSE_PROFILES, DEFAULT_THREAD_PAUSE_PROFILE,
    IMAGE_GENERATION_PROBABILITY, GEMINI_MODEL_PRIORITY,
)
from src.utils import (
    load_seen_articles, update_seen_articles, fetch_news,
    get_link_metadata, prune_pioneer_recent,
)
from src.agents import (
    generate_content, handle_interactions, generate_post_image,
    filter_available_models, select_pioneer_topic,
)
from src.broadcasters import post_to_bluesky, post_to_mastodon
from src.logger import SafeLogger
from src.settings import Settings, SettingsValidationError
from src.bluesky_session import load_or_login

load_dotenv()


@dataclass(frozen=True)
class ModeSelectionPayload:
    mode: str
    current_hour_utc: int


@dataclass(frozen=True)
class ContentPrepPayload:
    mode: str
    seen_data: Dict[str, List[str]]
    news_items: List[Dict[str, Any]]
    link_meta: Optional[Dict[str, Any]]
    bsky_client: Any
    recent_posts: List[str]
    pioneer_entry: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class BroadcastPayload:
    mode: str
    seen_data: Dict[str, List[str]]
    news_items: List[Dict[str, Any]]
    content_list: List[str]
    chosen_topic: str
    thread_pause_profile: str
    bsky_broadcast_client: Any
    pioneer_entry: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class AutomationPayload:
    mode: str
    seen_data: Dict[str, List[str]]
    news_items: List[Dict[str, Any]]
    pioneer_entry: Optional[Dict[str, Any]] = None


async def get_recent_posts(client, handle: str) -> List[str]:
    """Fetched recent posts silently from a logged-in client."""
    try:
        response = await client.app.bsky.feed.get_author_feed(actor=handle, limit=10)
        return [p.post.record.text for p in response.feed if hasattr(p.post.record, 'text')]
    except Exception as e:
        SafeLogger.error("recent_posts_fetch_failed", "Failed to fetch recent posts", exception=e, platform="bluesky")
        return []

async def apply_humanized_post_delay(settings: Settings):
    """Inject pre-post timing jitter so runs are less clock-perfect."""
    if settings.platform.post_jitter_max_seconds <= 0:
        return
    lower = max(0, settings.platform.post_jitter_min_seconds)
    upper = max(lower, settings.platform.post_jitter_max_seconds)
    wait_seconds = random.randint(lower, upper)
    if wait_seconds == 0:
        return
    SafeLogger.info("post_delay_applied", "Humanized delay before posting", attempt=1, platform="system", wait_seconds=wait_seconds)
    await asyncio.sleep(wait_seconds)

def load_settings_or_exit() -> Settings:
    try:
        return Settings.from_env()
    except SettingsValidationError as exc:
        SafeLogger.error("settings_validation_failed", "Configuration error", exception=exc, platform="system")
        invalid_variables = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]+\b", str(exc))))
        if invalid_variables:
            print(f"Configuration error: {exc} (invalid: {', '.join(invalid_variables)})")
        else:
            print(f"Configuration error: {exc}")
        sys.exit(1)


async def mode_selection_stage() -> ModeSelectionPayload:
    current_hour = datetime.now(timezone.utc).hour
    mode = "curator" if current_hour < 11 else "mentor"
    SafeLogger.configure(mode=mode)
    SafeLogger.info("mode_selected", "Execution mode selected", mode=mode)
    return ModeSelectionPayload(mode=mode, current_hour_utc=current_hour)


async def content_prep_stage(mode_payload: ModeSelectionPayload, creds: Any) -> ContentPrepPayload:
    mode = mode_payload.mode
    seen_data = load_seen_articles()

    news_items = []
    link_meta = None
    if mode == "curator":
        news_items = await fetch_news(seen_data["links"], seen_data["recent_topics"])
        if len(news_items) < 3:
            SafeLogger.warn("news_volume_low", "Low high-signal news volume, shifting mode", mode=mode, selected_items=len(news_items))
            mode = "strategist"
            SafeLogger.configure(mode=mode)
        else:
            # Sage 4.5: Scrape metadata for the top item for 'Rich Link Previews'
            link_meta = await get_link_metadata(news_items[0]['link'])

    from atproto import AsyncClient
    bsky_client = None
    recent_posts = []
    try:
        bsky_client = AsyncClient()
        await load_or_login(bsky_client, creds.bluesky_username, creds.bluesky_password)
        recent_posts = await get_recent_posts(bsky_client, creds.bluesky_username)
    except Exception as exc:
        bsky_client = None
        SafeLogger.error(
            "bluesky_preflight_failed",
            "Bluesky preflight failed; continuing with fallback context",
            platform="bluesky",
            error_type=type(exc).__name__,
            fallback_recent_posts_count=0,
        )

    # Pioneer dimension only attaches to Mentor/Strategist (afternoon).
    pioneer_entry = None
    if mode != "curator":
        pioneer_entry = select_pioneer_topic(seen_data)

    return ContentPrepPayload(
        mode=mode,
        seen_data=seen_data,
        news_items=news_items,
        link_meta=link_meta,
        bsky_client=bsky_client,
        recent_posts=recent_posts,
        pioneer_entry=pioneer_entry,
    )


async def broadcasting_stage(content_prep: ContentPrepPayload, settings: Settings, creds: Any, active_models: Optional[List[str]] = None) -> BroadcastPayload:
    mode = content_prep.mode
    news_items = content_prep.news_items
    link_meta = content_prep.link_meta
    content_list, chosen_topic = await generate_content(
        creds.gemini_api_key,
        content_prep.recent_posts,
        mode=mode,
        news_items=news_items,
        model_priority=active_models,
        pioneer_entry=content_prep.pioneer_entry,
    )

    # Generate image for non-Curator modes at configured probability.
    # Pass the finished thread so _craft_visual_prompt can tailor the Imagen prompt.
    image_bytes = None
    if mode != "curator" and random.random() < IMAGE_GENERATION_PROBABILITY:
        image_bytes = await generate_post_image(
            creds.gemini_api_key, chosen_topic, thread_posts=content_list
        )

    thread_pause_profile = random.choice(list(THREAD_PAUSE_PROFILES.keys())) if THREAD_PAUSE_PROFILES else DEFAULT_THREAD_PAUSE_PROFILE
    await apply_humanized_post_delay(settings)

    # Reuse the preflight client; only create a new session if preflight failed
    bsky_client = content_prep.bsky_client
    if bsky_client is None:
        from atproto import AsyncClient
        try:
            bsky_client = AsyncClient()
            await load_or_login(bsky_client, creds.bluesky_username, creds.bluesky_password)
        except Exception as exc:
            SafeLogger.error(
                "bluesky_broadcast_login_failed",
                "Bluesky login for broadcast failed",
                exception=exc,
                platform="bluesky",
            )
            bsky_client = None

    SafeLogger.info("broadcast_started", "Initiating concurrent delivery", platform="multi")

    broadcast_tasks = []
    if bsky_client is not None:
        broadcast_tasks.append(post_to_bluesky(
            bsky_client, content_list, link_meta,
            image_bytes=image_bytes,
            thread_pause_profile=thread_pause_profile
        ))
    else:
        SafeLogger.error(
            "bluesky_broadcast_skipped",
            "Bluesky broadcast skipped — no authenticated client available",
            platform="bluesky",
        )
    broadcast_tasks.append(post_to_mastodon(
        creds.mastodon_access_token, creds.mastodon_api_base_url, content_list,
        image_bytes=image_bytes,
        thread_pause_profile=thread_pause_profile
    ))

    results = await asyncio.gather(*broadcast_tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            SafeLogger.error("broadcast_task_failed", "Broadcast task failed", exception=r, platform="multi")

    if bsky_client is not None:
        bsky_broadcast_client = results[0] if not isinstance(results[0], Exception) else content_prep.bsky_client
    else:
        bsky_broadcast_client = content_prep.bsky_client
    return BroadcastPayload(
        mode=mode,
        seen_data=content_prep.seen_data,
        news_items=news_items,
        content_list=content_list,
        chosen_topic=chosen_topic,
        thread_pause_profile=thread_pause_profile,
        bsky_broadcast_client=bsky_broadcast_client,
        pioneer_entry=content_prep.pioneer_entry,
    )


async def post_run_automation_stage(broadcast: BroadcastPayload, creds: Any) -> AutomationPayload:
    automation_tasks = []
    if broadcast.bsky_broadcast_client is not None:
        automation_tasks.append(
            handle_interactions(broadcast.bsky_broadcast_client, creds.bluesky_username, creds.gemini_api_key)
        )
    await asyncio.gather(*automation_tasks, return_exceptions=True)

    return AutomationPayload(
        mode=broadcast.mode,
        seen_data=broadcast.seen_data,
        news_items=broadcast.news_items,
        pioneer_entry=broadcast.pioneer_entry,
    )


async def persistence_stage(automation: AutomationPayload) -> None:
    mode = automation.mode
    news_items = automation.news_items
    seen_data = automation.seen_data
    pioneer_entry = automation.pioneer_entry

    dirty = False
    if mode == "curator" and news_items:
        seen_data["links"] = (seen_data["links"] + [i['link'] for i in news_items])[-200:]

        # Sense topic to update memory
        topic_cat = news_items[0].get('detected_topic', 'General')
        if topic_cat != 'General':
            seen_data["recent_topics"] = (seen_data["recent_topics"] + [topic_cat])[-5:]
        dirty = True

    # Pioneer cooldown bookkeeping — only when a pioneer post actually fired
    if pioneer_entry:
        existing = prune_pioneer_recent(seen_data.get("pioneer_recent", []) or [])
        existing.append({
            "id": pioneer_entry["entry"]["id"],
            "posted_at": datetime.now(timezone.utc).isoformat(),
        })
        seen_data["pioneer_recent"] = existing
        dirty = True

    if dirty:
        update_seen_articles(lambda _: seen_data)


async def main():
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    SafeLogger.configure(run_id=run_id, platform="system")
    SafeLogger.info("run_started", "--- AskFred Engine v4.15.0 ---")

    settings = load_settings_or_exit()
    creds = settings.credentials

    # Prune the model priority list to what the Gemini API actually offers.
    # Non-fatal: if discovery fails the configured list is used unchanged.
    active_models = await filter_available_models(creds.gemini_api_key, GEMINI_MODEL_PRIORITY)

    mode_payload = await mode_selection_stage()
    content_prep = await content_prep_stage(mode_payload, creds)
    broadcast = await broadcasting_stage(content_prep, settings, creds, active_models=active_models)
    automation = await post_run_automation_stage(broadcast, creds)
    await persistence_stage(automation)

    SafeLogger.info("run_completed", "Intelligence cycle complete", mode=automation.mode, platform="system")

if __name__ == "__main__":
    asyncio.run(main())
