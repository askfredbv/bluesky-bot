import asyncio
import time
import random
import httpx
from typing import List, Optional, Dict, Any
from atproto import AsyncClient, models
from mastodon import Mastodon
from src.config import MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON
from src.utils import retry_with_backoff
from src.logger import SafeLogger

@retry_with_backoff
async def post_to_bluesky(username, password, content_list: List[str], link_meta: Optional[Dict[str, Any]] = None):
    """Async broadcaster for Bluesky supporting Rich Link Previews (External Embeds)."""
    print(f"Broadcasting to Bluesky (@{username})...")
    client = AsyncClient()
    await client.login(username, password)
    
    parent_ref = None
    root_ref = None
    
    for i, post_text in enumerate(content_list):
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
        if len(content_list) > 1 and i < len(content_list) - 1:
            await asyncio.sleep(random.uniform(1.0, 3.0))
    
    return client

@retry_with_backoff
async def post_to_mastodon(access_token: str, api_base_url: str, content_list: List[str]):
    """Async-wrapped broadcaster for Mastodon."""
    if not access_token: return
    
    def _sync_post():
        mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
        last_id = None
        for i, post_text in enumerate(content_list):
            status = mastodon.status_post(
                status=post_text,
                in_reply_to_id=last_id,
                visibility='public'
            )
            last_id = status['id']
            if len(content_list) > 1:
                time.sleep(random.uniform(1.0, 3.0))
            
    await asyncio.to_thread(_sync_post)


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
