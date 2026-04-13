import asyncio
from typing import List, Optional
from atproto import AsyncClient, models
from mastodon import Mastodon
from src.config import MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON
from src.utils import retry_with_backoff
from src.logger import SafeLogger
import random

@retry_with_backoff
async def post_to_bluesky(username, password, content_list: List[str], image_data: Optional[bytes] = None, image_alt: str = ""):
    """Async broadcaster for Bluesky using AsyncClient."""
    print(f"Broadcasting to Bluesky (@{username}) [Async]...")
    client = AsyncClient()
    await client.login(username, password)
    
    parent_ref = None
    root_ref = None
    
    for i, post_text in enumerate(content_list):
        embed = None
        if i == 0 and image_data:
            upload = await client.upload_blob(image_data)
            embed = models.AppBskyEmbedImages.Main(
                images=[models.AppBskyEmbedImages.Image(alt=image_alt, image=upload.blob)]
            )
        
        if not parent_ref:
            post = await client.send_post(text=post_text, embed=embed)
            root_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
        else:
            reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
            post = await client.send_post(text=post_text, reply_to=reply_ref)
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
        
        # Fortress v4.4: Intra-thread jitter
        if len(content_list) > 1 and i < len(content_list) - 1:
            await asyncio.sleep(random.uniform(1.0, 3.0))
    
    return client

@retry_with_backoff
async def post_to_mastodon(access_token: str, api_base_url: str, content_list: List[str], image_data: Optional[bytes] = None, image_alt: str = ""):
    """Async-wrapped broadcaster for Mastodon."""
    if not access_token:
        print("Skipping Mastodon: No access token.")
        return
    
    print(f"Broadcasting to Mastodon ({api_base_url}) [Parallel]...")
    
    def _sync_post():
        mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
        media_ids = []
        if image_data:
            media = mastodon.media_post(image_data, mime_type="image/jpeg", description=image_alt)
            media_ids.append(media)
        
        last_id = None
        for i, post_text in enumerate(content_list):
            status = mastodon.status_post(
                status=post_text,
                in_reply_to_id=last_id,
                media_ids=media_ids if i == 0 else None,
                visibility='public'
            )
            last_id = status['id']
            # Fortress v4.4: Intra-thread jitter
            if len(content_list) > 1:
                import time
                time.sleep(random.uniform(1.0, 3.0))
            
    await asyncio.to_thread(_sync_post)

async def update_profile_bio(client: AsyncClient, bio_text: str):
    """Update only the bio text, preserving existing metadata asynchronously."""
    try:
        # Fetch existing profile record
        profile_record = (await client.com.atproto.repo.get_record(
            collection='app.bsky.actor.profile',
            repo=client.me.did,
            rkey='self'
        )).value

        # Update description in the record dictionary
        profile_dict = profile_record.copy() if hasattr(profile_record, 'copy') else dict(profile_record)
        profile_dict['description'] = bio_text

        # Put the record back
        await client.com.atproto.repo.put_record(
            collection='app.bsky.actor.profile',
            repo=client.me.did,
            rkey='self',
            record=profile_dict
        )
        print("Updated Bluesky profile bio (v4.3 Async Sync).")
    except Exception as e:
        SafeLogger.error("Failed to update Bluesky bio", e)

async def update_profile_bio_mastodon(token: str, api_url: str, bio_text: str):
    """Update Mastodon bio asynchronously."""
    if not token: return
    try:
        def _sync_bio():
            mastodon = Mastodon(access_token=token, api_base_url=api_url)
            mastodon.account_update_credentials(note=bio_text)
        await asyncio.to_thread(_sync_bio)
        print("Updated Mastodon profile bio.")
    except Exception as e:
        SafeLogger.error("Failed to update Mastodon bio", e)
