import asyncio
import io
import random
from typing import List, Optional, Dict, Any
from atproto import AsyncClient, models
from mastodon import Mastodon
from src.config import (
    MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON,
    THREAD_PAUSE_PROFILES, DEFAULT_THREAD_PAUSE_PROFILE
)
from src.utils import classify_retry, sleep_for_rate_limit, sleep_for_transient
from src.logger import SafeLogger
from src.facets import build_facets
from src.metrics import BroadcastResult

MASTODON_POST_TIMEOUT_SECONDS = 20.0
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

def _enforce_post_length_invariant(content_list: List[str], max_length: int, platform_name: str) -> bool:
    """Hard invariant check — all posts must fit the platform limit.

    v4.15.3: replaces the old ``_split_and_constrain_posts`` word-boundary
    splitter. Length is now enforced at generation time via the
    ``max_output_tokens`` cap and the hard-reject in ``_validate_thread_shape``.
    If any post still overshoots here, the upstream invariant failed — we
    log ``broadcast_invariant_violated`` and skip this platform for the run.
    Missing one run beats posting a mid-sentence bot tell.

    Returns True when every post fits, False when the invariant was violated.
    """
    for idx, post in enumerate(content_list):
        if len(post) > max_length:
            SafeLogger.error(
                "broadcast_invariant_violated",
                f"Post {idx} exceeds {platform_name} limit; skipping this platform's broadcast",
                platform=platform_name.lower(),
                post_index=idx,
                length=len(post),
                max_length=max_length,
            )
            return False
    return True

def _sample_thread_pause(profile_name: str) -> float:
    """Sample a human-like pause between thread posts using named rhythm profiles."""
    low, high = THREAD_PAUSE_PROFILES.get(
        profile_name, THREAD_PAUSE_PROFILES[DEFAULT_THREAD_PAUSE_PROFILE]
    )
    return random.uniform(low, high)

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

    v4.16 (Phase 1 Step 3b): retry is per-post, not per-thread. The two retry
    budgets (rate-limit, transient) are initialised once and shared across
    every post in the thread — a 5-post × 3-retry budget would otherwise add
    up to 45 min of 429-backoff sleeps. When the budget is exhausted, the
    thread stops cleanly with the posts already sent left on the wire and
    ``bluesky_partial_delivery`` logged. The old ``@retry_with_backoff``
    decorator re-ran the whole function on failure, causing silent re-sends.
    """
    SafeLogger.info("broadcast_started", "Broadcasting to Bluesky", platform="bluesky")

    if not _enforce_post_length_invariant(content_list, MAX_POST_LENGTH_BSKY, "Bluesky"):
        # Invariant violated; skip broadcast, preserve client for downstream
        return BroadcastResult(client=client, sent_uris=[], error=None)

    parent_ref = None
    root_ref = None
    sent_uris: List[str] = []
    total_posts = len(content_list)

    # Per-thread retry budgets — shared across every post in the thread.
    rate_limit_attempts = 0
    transient_attempts = 0

    async def _send_with_thread_retry(send_fn):
        nonlocal rate_limit_attempts, transient_attempts
        while True:
            try:
                return await send_fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if classify_retry(e) == "rate_limit":
                    rate_limit_attempts += 1
                    await sleep_for_rate_limit(rate_limit_attempts, e, function="post_to_bluesky")
                else:
                    transient_attempts += 1
                    await sleep_for_transient(transient_attempts, e, function="post_to_bluesky")

    try:
        for i, post_text in enumerate(content_list):
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

            # Closure capturing the per-post args; reply_ref is computed here so
            # a retry reads the current parent_ref (not a stale one from a prior post).
            if parent_ref is None:
                async def _do_send():
                    return await client.send_post(text=post_text, embed=embed, facets=facets)
            else:
                reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
                async def _do_send():
                    return await client.send_post(text=post_text, reply_to=reply_ref, facets=facets)

            post = await _send_with_thread_retry(_do_send)

            if root_ref is None:
                root_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=post.cid, uri=post.uri)
            sent_uris.append(post.uri)

            # Intra-thread jitter
            if total_posts > 1 and i < total_posts - 1:
                await asyncio.sleep(_sample_thread_pause(thread_pause_profile))
    except asyncio.CancelledError:
        if len(sent_uris) < total_posts:
            SafeLogger.warn(
                "bluesky_partial_delivery",
                "Bluesky thread partially delivered",
                platform="bluesky",
                posted=len(sent_uris),
                total=total_posts,
                reason="cancelled",
            )
        raise
    except Exception as e:
        SafeLogger.warn(
            "bluesky_partial_delivery",
            "Bluesky thread partially delivered",
            platform="bluesky",
            posted=len(sent_uris),
            total=total_posts,
            reason="error",
            error_type=type(e).__name__,
        )
        return BroadcastResult(client=client, sent_uris=sent_uris, error=e)

    return BroadcastResult(client=client, sent_uris=sent_uris, error=None)

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

    v4.16 (Phase 1 Step 3b): per-post retry with shared per-thread budgets.
    The hand-rolled ``min(2^n, 5)`` sleep has been swapped for the unified
    ``sleep_for_rate_limit`` / ``sleep_for_transient`` helpers — Mastodon
    now honours ``X-RateLimit-Reset`` instead of hammering on 429.
    On budget exhaustion the thread stops cleanly with earlier posts
    intact and ``mastodon_partial_delivery`` logged.
    """
    if not access_token:
        return BroadcastResult(client=None, sent_uris=[], error=None)

    if not _enforce_post_length_invariant(content_list, MAX_POST_LENGTH_MASTODON, "Mastodon"):
        # Invariant violated; skip broadcast.
        return BroadcastResult(client=None, sent_uris=[], error=None)

    mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
    total_posts = len(content_list)
    posted_count = 0
    last_id = None
    sent_ids: List[str] = []

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

    async def _status_post_with_timeout(post_text: str, reply_to_id: Optional[str], media: Optional[List[str]] = None):
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

    # Per-thread retry budgets — shared across every post in the thread.
    rate_limit_attempts = 0
    transient_attempts = 0

    async def _send_with_thread_retry(send_fn):
        nonlocal rate_limit_attempts, transient_attempts
        while True:
            try:
                return await send_fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if classify_retry(e) == "rate_limit":
                    rate_limit_attempts += 1
                    await sleep_for_rate_limit(rate_limit_attempts, e, function="post_to_mastodon")
                else:
                    transient_attempts += 1
                    await sleep_for_transient(transient_attempts, e, function="post_to_mastodon")

    try:
        for i, post_text in enumerate(content_list):
            attach = media_ids if i == 0 else None

            async def _do_send(pt=post_text, rid=last_id, m=attach):
                return await _status_post_with_timeout(pt, rid, media=m)

            status = await _send_with_thread_retry(_do_send)
            last_id = status.get('id') if isinstance(status, dict) else getattr(status, "id", None)
            if last_id is not None:
                sent_ids.append(str(last_id))
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
        SafeLogger.warn(
            "mastodon_partial_delivery",
            "Mastodon thread partially delivered",
            platform="mastodon",
            posted=posted_count,
            total=total_posts,
            reason="error",
            error_type=type(e).__name__,
        )
        return BroadcastResult(client=None, sent_uris=sent_ids, error=e)

    return BroadcastResult(client=None, sent_uris=sent_ids, error=None)


