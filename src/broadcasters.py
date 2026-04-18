import asyncio
import io
import random
import httpx
import re
from typing import List, Optional, Dict, Any
from atproto import AsyncClient, models
from mastodon import Mastodon
from src.config import (
    MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON,
    THREAD_PAUSE_PROFILES, DEFAULT_THREAD_PAUSE_PROFILE
)
from src.utils import retry_with_backoff
from src.logger import SafeLogger
from src.facets import build_facets

MASTODON_POST_TIMEOUT_SECONDS = 20.0
MASTODON_POST_MAX_ATTEMPTS = 3
_MASTODON_IMAGE_MAX_BYTES = 8 * 1024 * 1024  # Mastodon default cap — 8 MB


def _detect_image_mime(data: bytes) -> str:
    """Detect MIME type from image bytes via Pillow; default to PNG."""
    try:
        from PIL import Image
        fmt = (Image.open(io.BytesIO(data)).format or "PNG").lower()
        return {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp"}.get(fmt, "image/png")
    except Exception:
        return "image/png"

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
            "content_chunk_split",
            f"{platform_name} content exceeded max length; splitting into safe chunks.",
            platform=platform_name.lower(),
            max_length=max_length
        )
        remaining = original_text.strip()
        while remaining:
            if len(remaining) <= max_length:
                constrained_posts.append(remaining)
                break
            split_at = remaining.rfind(" ", 0, max_length)
            if split_at == -1:
                split_at = max_length  # hard fallback: no spaces in chunk
            chunk = remaining[:split_at].rstrip()
            constrained_posts.append(chunk)
            remaining = remaining[split_at:].lstrip()

    return constrained_posts

def _sample_thread_pause(profile_name: str) -> float:
    """Sample a human-like pause between thread posts using named rhythm profiles."""
    low, high = THREAD_PAUSE_PROFILES.get(
        profile_name, THREAD_PAUSE_PROFILES[DEFAULT_THREAD_PAUSE_PROFILE]
    )
    return random.uniform(low, high)

@retry_with_backoff
async def post_to_bluesky(
    client: AsyncClient,
    content_list: List[str],
    link_meta: Optional[Dict[str, Any]] = None,
    image_bytes: Optional[bytes] = None,
    thread_pause_profile: str = DEFAULT_THREAD_PAUSE_PROFILE
):
    """Async broadcaster for Bluesky supporting Rich Link Previews (External Embeds).

    The client must already be authenticated before calling this function.
    Login is performed once upstream (content_prep_stage) and reused here.
    """
    SafeLogger.info("broadcast_started", "Broadcasting to Bluesky", platform="bluesky")

    constrained_content_list = _split_and_constrain_posts(
        content_list, MAX_POST_LENGTH_BSKY, "Bluesky"
    )

    parent_ref = None
    root_ref = None

    for i, post_text in enumerate(constrained_content_list):
        embed = None
        if i == 0:
            if image_bytes:
                # Image embed (Mentor/Strategist) — Bluesky hard limit is 1 MB per image
                _BLUESKY_IMAGE_MAX_BYTES = 976 * 1024  # 976 KB — safe margin under 1 MB
                if len(image_bytes) > _BLUESKY_IMAGE_MAX_BYTES:
                    SafeLogger.warn(
                        "image_too_large",
                        "Generated image exceeds Bluesky 1 MB limit; skipping image attach",
                        platform="bluesky",
                        size_bytes=len(image_bytes),
                    )
                else:
                    try:
                        upload = await client.upload_blob(image_bytes)
                        embed = models.AppBskyEmbedImages.Main(
                            images=[models.AppBskyEmbedImages.Image(
                                image=upload.blob,
                                alt=f"Illustration: {content_list[0][:100]}"
                            )]
                        )
                    except Exception as e:
                        SafeLogger.error(
                            "image_upload_failed",
                            "Failed to upload generated image to Bluesky",
                            exception=e,
                            platform="bluesky",
                        )
            elif link_meta:
                # Link card (Curator)
                thumb_blob = None
                if link_meta.get('image_data'):
                    try:
                        upload = await client.upload_blob(link_meta['image_data'])
                        thumb_blob = upload.blob
                    except Exception as e:
                        SafeLogger.error(
                            "thumbnail_upload_failed",
                            "Failed to upload link preview thumbnail",
                            exception=e,
                            platform="bluesky",
                        )
                embed = models.AppBskyEmbedExternal.Main(
                    external=models.AppBskyEmbedExternal.External(
                        title=link_meta.get('title', 'Technical Insight'),
                        description=link_meta.get('description', ''),
                        uri=link_meta.get('url', ''),
                        thumb=thumb_blob
                    )
                )
        
        facets = build_facets(post_text) or None

        if not parent_ref:
            post = await client.send_post(text=post_text, embed=embed, facets=facets)
            root_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
        else:
            reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
            post = await client.send_post(text=post_text, reply_to=reply_ref, facets=facets)
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
    image_bytes: Optional[bytes] = None,
    thread_pause_profile: str = DEFAULT_THREAD_PAUSE_PROFILE
):
    """Async-wrapped broadcaster for Mastodon.

    When image_bytes is provided, attaches the image to the first post only
    (Mastodon threads mirror Bluesky: media attaches to the root post).
    """
    if not access_token: return

    constrained_content_list = _split_and_constrain_posts(
        content_list, MAX_POST_LENGTH_MASTODON, "Mastodon"
    )

    mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
    total_posts = len(constrained_content_list)
    posted_count = 0
    last_id = None

    # Upload image for first-post attachment (if provided and within size cap)
    media_ids = None
    if image_bytes:
        if len(image_bytes) > _MASTODON_IMAGE_MAX_BYTES:
            SafeLogger.warn(
                "mastodon_image_too_large",
                "Generated image exceeds Mastodon size cap; skipping image attach",
                platform="mastodon",
                size_bytes=len(image_bytes),
            )
        else:
            try:
                mime = _detect_image_mime(image_bytes)
                alt = f"Illustration: {content_list[0][:100]}" if content_list else "Illustration"
                media = await asyncio.to_thread(
                    mastodon.media_post, image_bytes,
                    mime_type=mime, description=alt
                )
                media_id = media.get('id') if isinstance(media, dict) else getattr(media, 'id', None)
                if media_id:
                    media_ids = [media_id]
            except Exception as e:
                SafeLogger.warn(
                    "mastodon_media_upload_failed",
                    "Failed to upload image to Mastodon; posting without image",
                    platform="mastodon",
                    error_type=type(e).__name__,
                )

    async def _status_post_with_timeout_and_retry(post_text: str, reply_to_id: Optional[str], media: Optional[List[str]] = None):
        for attempt in range(1, MASTODON_POST_MAX_ATTEMPTS + 1):
            try:
                kwargs = {
                    "status": post_text,
                    "in_reply_to_id": reply_to_id,
                    "visibility": 'public',
                }
                if media:
                    kwargs["media_ids"] = media
                return await asyncio.wait_for(
                    asyncio.to_thread(mastodon.status_post, **kwargs),
                    timeout=MASTODON_POST_TIMEOUT_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt == MASTODON_POST_MAX_ATTEMPTS:
                    raise
                SafeLogger.warn(
                    "mastodon_post_retry",
                    "Retrying Mastodon post after failure",
                    platform="mastodon",
                    attempt=attempt,
                    max_attempts=MASTODON_POST_MAX_ATTEMPTS,
                    error_type=type(e).__name__
                )
                await asyncio.sleep(min(2 ** attempt, 5))

    try:
        for i, post_text in enumerate(constrained_content_list):
            attach = media_ids if i == 0 else None
            status = await _status_post_with_timeout_and_retry(post_text, last_id, media=attach)
            last_id = status.get('id') if isinstance(status, dict) else getattr(status, "id", None)
            posted_count += 1

            if total_posts > 1 and i < total_posts - 1:
                await asyncio.sleep(_sample_thread_pause(thread_pause_profile))
    except asyncio.CancelledError:
        if posted_count < total_posts:
            SafeLogger.warn(
                "mastodon_partial_delivery",
                "Mastodon thread partially delivered",
                platform="mastodon",
                posted=posted_count,
                total=total_posts,
                reason="cancelled"
            )
        raise
    except Exception as e:
        if posted_count < total_posts:
            SafeLogger.warn(
                "mastodon_partial_delivery",
                "Mastodon thread partially delivered",
                platform="mastodon",
                posted=posted_count,
                total=total_posts,
                reason="error",
                error_type=type(e).__name__
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
        SafeLogger.error("bluesky_bio_update_failed", "Failed to update Bluesky bio", exception=e, platform="bluesky")

async def update_profile_bio_mastodon(token: str, api_url: str, bio_text: str):
    if not token: return
    try:
        def _sync_bio():
            mastodon = Mastodon(access_token=token, api_base_url=api_url)
            mastodon.account_update_credentials(note=bio_text)
        await asyncio.to_thread(_sync_bio)
    except Exception as e:
        SafeLogger.error("mastodon_bio_update_failed", "Failed to update Mastodon bio", exception=e, platform="mastodon")
