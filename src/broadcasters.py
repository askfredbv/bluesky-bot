import asyncio
import random
import httpx
from typing import List, Optional, Dict, Any
from atproto import AsyncClient, models
from mastodon import Mastodon
from src.config import (
    MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON,
    THREAD_PAUSE_PROFILES, DEFAULT_THREAD_PAUSE_PROFILE
)
from src.utils import retry_with_backoff
from src.logger import SafeLogger

MASTODON_POST_TIMEOUT_SECONDS = 20.0
MASTODON_POST_MAX_ATTEMPTS = 3

def _split_and_constrain_posts(content_list: List[str], max_length: int, platform_name: str) -> List[str]:
    """
    Ensure all outbound post chunks are <= max_length.
    Splits overlong entries into multiple posts and emits a warning when this happens.
    """
    constrained_posts: List[str] = []

    for original_text in content_list:
        if len(original_text) <= max_length:
            constrained_posts.append(original_text)
            continue

        SafeLogger.warn(
            f"{platform_name} content exceeded {max_length} chars; splitting into safe chunks."
        )
        for i in range(0, len(original_text), max_length):
            constrained_posts.append(original_text[i:i + max_length])

    return constrained_posts

def _sample_thread_pause(profile_name: str) -> float:
    """Sample a human-like pause between thread posts using named rhythm profiles."""
    low, high = THREAD_PAUSE_PROFILES.get(
        profile_name, THREAD_PAUSE_PROFILES[DEFAULT_THREAD_PAUSE_PROFILE]
    )
    return random.uniform(low, high)

@retry_with_backoff
async def post_to_bluesky(
    username,
    password,
    content_list: List[str],
    link_meta: Optional[Dict[str, Any]] = None,
    thread_pause_profile: str = DEFAULT_THREAD_PAUSE_PROFILE
):
    """Async broadcaster for Bluesky supporting Rich Link Previews (External Embeds)."""
    print(f"Broadcasting to Bluesky (@{username})...")
    client = AsyncClient()
    await client.login(username, password)

    constrained_content_list = _split_and_constrain_posts(
        content_list, MAX_POST_LENGTH_BSKY, "Bluesky"
    )

    parent_ref = None
    root_ref = None

    for i, post_text in enumerate(constrained_content_list):
        embed = None
        # Sage 4.5: Attach Rich Link Preview (External Embed) to the first post
        if i == 0 and link_meta:
            thumb_blob = None
            if link_meta.get('image_data'):
                try:
                    upload = await client.upload_blob(link_meta['image_data'])
                    thumb_blob = upload.blob
                except Exception as e:
                    SafeLogger.error("Failed to upload Link Preview thumbnail", e)
            
            embed = models.AppBskyEmbedExternal.Main(
                external=models.AppBskyEmbedExternal.External(
                    title=link_meta.get('title', 'Technical Insight'),
                    description=link_meta.get('description', ''),
                    uri=link_meta.get('url', ''),
                    thumb=thumb_blob
                )
            )
        
        if not parent_ref:
            post = await client.send_post(text=post_text, embed=embed)
            root_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
        else:
            reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
            post = await client.send_post(text=post_text, reply_to=reply_ref)
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
        
        # Intra-thread jitter
        if len(constrained_content_list) > 1 and i < len(constrained_content_list) - 1:
            await asyncio.sleep(_sample_thread_pause(thread_pause_profile))
    
    return client

@retry_with_backoff
async def post_to_mastodon(
    access_token: str,
    api_base_url: str,
    content_list: List[str],
    thread_pause_profile: str = DEFAULT_THREAD_PAUSE_PROFILE
):
    """Async-wrapped broadcaster for Mastodon."""
    if not access_token: return

    constrained_content_list = _split_and_constrain_posts(
        content_list, MAX_POST_LENGTH_MASTODON, "Mastodon"
    )

    mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
    total_posts = len(constrained_content_list)
    posted_count = 0
    last_id = None

    async def _status_post_with_timeout_and_retry(post_text: str, reply_to_id: Optional[str]):
        for attempt in range(1, MASTODON_POST_MAX_ATTEMPTS + 1):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        mastodon.status_post,
                        status=post_text,
                        in_reply_to_id=reply_to_id,
                        visibility='public'
                    ),
                    timeout=MASTODON_POST_TIMEOUT_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt == MASTODON_POST_MAX_ATTEMPTS:
                    raise
                SafeLogger.warn(
                    f"Mastodon post retry: "
                    f"{{'attempt': {attempt}, 'max_attempts': {MASTODON_POST_MAX_ATTEMPTS}, "
                    f"'error_type': '{type(e).__name__}'}}"
                )
                await asyncio.sleep(min(2 ** attempt, 5))

    try:
        for i, post_text in enumerate(constrained_content_list):
            status = await _status_post_with_timeout_and_retry(post_text, last_id)
            last_id = status.get('id') if isinstance(status, dict) else getattr(status, "id", None)
            posted_count += 1

            if total_posts > 1 and i < total_posts - 1:
                await asyncio.sleep(_sample_thread_pause(thread_pause_profile))
    except asyncio.CancelledError:
        if posted_count < total_posts:
            SafeLogger.warn(
                f"Mastodon thread partial delivery: "
                f"{{'posted': {posted_count}, 'total': {total_posts}, 'reason': 'cancelled'}}"
            )
        raise
    except Exception as e:
        if posted_count < total_posts:
            SafeLogger.warn(
                f"Mastodon thread partial delivery: "
                f"{{'posted': {posted_count}, 'total': {total_posts}, "
                f"'reason': 'error', 'error_type': '{type(e).__name__}'}}"
            )
        raise


async def update_profile_bio(client: AsyncClient, bio_text: str):
    """Update only the bio text asynchronously."""
    try:
        profile_record = (await client.com.atproto.repo.get_record(collection='app.bsky.actor.profile', repo=client.me.did, rkey='self')).value
        profile_dict = profile_record.copy() if hasattr(profile_record, 'copy') else dict(profile_record)
        profile_dict['description'] = bio_text
        await client.com.atproto.repo.put_record(collection='app.bsky.actor.profile', repo=client.me.did, rkey='self', record=profile_dict)
    except Exception as e:
        SafeLogger.error("Failed to update Bluesky bio", e)

async def update_profile_bio_mastodon(token: str, api_url: str, bio_text: str):
    if not token: return
    try:
        def _sync_bio():
            mastodon = Mastodon(access_token=token, api_base_url=api_url)
            mastodon.account_update_credentials(note=bio_text)
        await asyncio.to_thread(_sync_bio)
    except Exception as e:
        SafeLogger.error("Failed to update Mastodon bio", e)
