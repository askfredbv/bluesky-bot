import asyncio
import io
import random
import re
import uuid
from urllib.parse import urlparse
from typing import List, Optional, Dict, Any
from atproto import AsyncClient, models
from mastodon import Mastodon
from src.config import (
    MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON,
    THREAD_PAUSE_PROFILES, DEFAULT_THREAD_PAUSE_PROFILE,
    MAX_HASHTAGS_PER_POST,
)
from src.retry import classify_retry, sleep_for_rate_limit, sleep_for_transient
from src.utils import compress_image_to_fit
from src.net_safety import canonical_url
from src.logger import SafeLogger
from src.facets import build_facets
from src.metrics import BroadcastResult

MASTODON_POST_TIMEOUT_SECONDS = 20.0
_MASTODON_IMAGE_MAX_BYTES = 8 * 1024 * 1024  # Mastodon default cap — 8 MB
_BLUESKY_IMAGE_MAX_BYTES = 976 * 1024        # 976 KB — safe margin under Bluesky's 1 MB blob limit


# Mastodon discovery tags (v4.26) — trailing anchor tags on the ROOT post only.
# Separator is a blank line: the fediverse convention is a tag line set off from
# the prose, and it keeps the tags out of the sentence the reader is reading.
_MASTODON_TAG_SEPARATOR = "\n\n"
_TAG_RE = re.compile(r"(?<!\w)#(\w+)")


def apply_mastodon_tags(
    content_list: List[str],
    tags: List[str],
    max_length: int = MAX_POST_LENGTH_MASTODON,
) -> List[str]:
    """Return ``content_list`` with discovery tags appended to the root post.

    Root post only: it is the one that gets boosted and the one that represents
    the thread in a tag timeline, and tagging every post in a thread spams that
    timeline with the same item.

    Three ways this declines to act, all silent and all correct:

    - **The shared ceiling.** MAX_HASHTAGS_PER_POST is a per-post total, not a
      per-source one. The generator is allowed its own inline hashtags, and
      this only ever spends the remainder — a root that already carries two
      gets none from here. Without that the two limits stacked and a post could
      ship four hashtags, breaking the STYLE_GUIDELINES ceiling on one platform
      only, which is exactly the divergence AGENTS.md #7 forbids (Codex review,
      2026-09-09).
    - **Duplicates.** A tag already in the root is dropped; repeating one reads
      as a bot tell.
    - **Length.** If the suffix would breach ``max_length`` the tags are shed
      one at a time until it fits, down to appending nothing. Content is never
      trimmed to make room: the post is the point, the tag is not.
    """
    if not content_list or not tags:
        return list(content_list)

    root = content_list[0]
    present = {t.lower() for t in _TAG_RE.findall(root)}
    allowance = MAX_HASHTAGS_PER_POST - len(present)
    if allowance <= 0:
        return list(content_list)

    candidates = [t for t in tags if t.lstrip("#").lower() not in present][:allowance]

    while candidates:
        suffix = _MASTODON_TAG_SEPARATOR + " ".join(candidates)
        if len(root) + len(suffix) <= max_length:
            return [root + suffix] + list(content_list[1:])
        candidates.pop()

    return list(content_list)


def number_mastodon_thread(
    content_list: List[str],
    max_length: int = MAX_POST_LENGTH_MASTODON,
) -> List[str]:
    """Return a multi-part thread with "1/2", "2/2" ... appended to each part.

    Mastodon lists a self-thread newest-first, so in a profile or home timeline
    part 2 arrives above part 1 under a "Continued thread" label, and a reader
    meets the continuation cold. Bluesky's client labels self-threads "1/2",
    "2/2" on its own (the labels are not in our post text), so Bluesky readers
    already had this and Mastodon readers did not. Measured on the Bluesky feed
    2026-09-10: 145 of 313 root posts were threads (18 of the last 60), and only
    31 of those 145 would fit in one 500-char Mastodon post, so collapsing
    threads is not the fix; numbering is.

    Call this AFTER ensure_mastodon_source_link and BEFORE apply_mastodon_tags.
    The marker ends the part's text, after an inline source link if there is
    one, and the tags must stay a paragraph of their own so Mastodon's web
    client still lifts them into its hashtag bar.

    Not a teaser (AGENTS.md #1 bans 🧵 and "thread incoming"): those announce
    parts that do not exist yet, while this labels a thread that is complete
    when its first part goes out. It carries no content of its own, which is
    what lets it clear the appended-after-validation bar in AGENTS.md #7.

    Single posts come back unchanged. All-or-nothing: if numbering any part
    would breach ``max_length`` the thread ships unnumbered, because one part
    missing its label reads worse than none having one.
    """
    total = len(content_list)
    if total < 2:
        return list(content_list)
    numbered = [f"{part.rstrip()} {i}/{total}" for i, part in enumerate(content_list, start=1)]
    if any(len(part) > max_length for part in numbered):
        return list(content_list)
    return numbered


# The Curator's source link on Mastodon (2026-09-10, freeze audit X6). Same
# separator the model uses when it does inline a link: a paragraph of its own.
_MASTODON_LINK_SEPARATOR = _MASTODON_TAG_SEPARATOR
_URL_TRAILING = '.,;:!?)]}"' + "'"


def _text_mentions_url(text: str, source_url: str) -> bool:
    """True if ``text`` already carries ``source_url`` in any common form.

    Compared canonically, so http/https, tracking parameters, a trailing slash
    and arXiv abs/pdf/version forms all count as the same link. A bare host/path
    mention without a scheme counts too.
    """
    target = canonical_url(source_url)
    for token in text.split():
        candidate = token.lstrip("(<[").rstrip(_URL_TRAILING)
        if candidate.startswith(("http://", "https://")) and canonical_url(candidate) == target:
            return True
    parsed = urlparse(source_url.strip())
    bare = (parsed.netloc + parsed.path).rstrip("/").lower()
    if bare.startswith("www."):
        bare = bare[4:]
    return bool(bare) and bare in text.lower()


def ensure_mastodon_source_link(
    content_list: List[str],
    source_url: Optional[str],
    max_length: int = MAX_POST_LENGTH_MASTODON,
) -> List[str]:
    """Make sure a Curator post's source link reaches Mastodon readers.

    Bluesky shows the source as a link card built from ``link_meta``, so the
    generated text does not need to contain the URL there. Mastodon can only
    show a link that is in the text. The Curator prompt asks for "THE LINK at
    the end", but nothing enforced it, and the live feed showed the result:
    4 of the last 5 Curator posts (2026-09-06 to 09-09) reached Mastodon with no
    link to the source at all, while Bluesky readers got a card each time.
    AGENTS.md #7 forbids exactly that divergence (no different link); the card
    versus a URL in the text is its permitted "embed shape" difference.

    Appends ``source_url`` to the root as its own paragraph when no part of the
    thread already carries it. Runs before number_mastodon_thread and
    apply_mastodon_tags. It is the same URL the Bluesky card carries, so it adds
    no claim the post does not already make.

    Declines rather than overflow: if the root plus the link would breach
    ``max_length`` the thread ships as generated and the skip is logged. The
    broadcast is never dropped for the sake of the link.
    """
    if not content_list or not source_url or not source_url.strip():
        return list(content_list)
    url = source_url.strip()
    if any(_text_mentions_url(part, url) for part in content_list):
        return list(content_list)
    root = content_list[0].rstrip() + _MASTODON_LINK_SEPARATOR + url
    if len(root) > max_length:
        SafeLogger.warn(
            "mastodon_source_link_too_long",
            "Source link would overflow the Mastodon root; shipping without it",
            platform="mastodon",
            root_length=len(content_list[0]),
            url_length=len(url),
        )
        return list(content_list)
    return [root] + list(content_list[1:])


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
    delivered: List[str] = []
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
                    # Image embed (Mentor/Strategist). The image model's output
                    # clusters around/above Bluesky's 1 MB blob limit, so
                    # compress to fit rather than drop (2026-06-14 fix — see
                    # compress_image_to_fit).
                    fitted, fits = compress_image_to_fit(image_bytes, _BLUESKY_IMAGE_MAX_BYTES)
                    if not fits:
                        SafeLogger.warn(
                            "image_too_large",
                            "Image still exceeds Bluesky 1 MB limit after compression; skipping attach",
                            platform="bluesky",
                            original_bytes=len(image_bytes),
                            compressed_bytes=len(fitted),
                        )
                    else:
                        try:
                            upload = await client.upload_blob(fitted)
                            embed = models.AppBskyEmbedImages.Main(
                                images=[models.AppBskyEmbedImages.Image(
                                    image=upload.blob,
                                    alt=f"Illustration: {content_list[0][:100]}"
                                )]
                            )
                            SafeLogger.info(
                                "image_attached",
                                "Generated image attached to Bluesky post",
                                platform="bluesky",
                                original_bytes=len(image_bytes),
                                final_bytes=len(fitted),
                                recompressed=fitted is not image_bytes,
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
            delivered.append(post_text)

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
        return BroadcastResult(
            client=client, sent_uris=sent_uris, error=e, delivered_texts=delivered
        )

    return BroadcastResult(
        client=client, sent_uris=sent_uris, error=None, delivered_texts=delivered
    )

async def post_to_mastodon(
    access_token: str,
    api_base_url: str,
    content_list: List[str],
    image_bytes: Optional[bytes] = None,
    thread_pause_profile: str = DEFAULT_THREAD_PAUSE_PROFILE,
    tags: Optional[List[str]] = None,
    source_url: Optional[str] = None,
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

    v4.26: ``tags`` are Mastodon-only discovery tags appended to the root
    post before the length invariant runs, so the check sees the text that
    actually ships. The generated content itself is untouched and identical
    to Bluesky's — see ``apply_mastodon_tags`` and AGENTS.md principle 7.

    2026-09-10: ``source_url`` is the link the Bluesky card carries. Mastodon
    has no card for it, so ``ensure_mastodon_source_link`` puts it in the root
    text when the generated text does not already include it.
    """
    if not access_token:
        return BroadcastResult(client=None, sent_uris=[], error=None)

    # Mastodon-only mechanics, in this order, all BEFORE the length invariant so
    # it measures the text that actually ships:
    #   1. the Curator's source link, when the text does not already carry it;
    #   2. "1/2" thread-position markers;
    #   3. discovery tags last, so they stay a hashtag-only final paragraph that
    #      Mastodon lifts into its tag bar.
    # Each step declines rather than overflow, so none should trip the check.
    content_list = ensure_mastodon_source_link(content_list, source_url)
    content_list = number_mastodon_thread(content_list)
    if tags:
        content_list = apply_mastodon_tags(content_list, tags)

    if not _enforce_post_length_invariant(content_list, MAX_POST_LENGTH_MASTODON, "Mastodon"):
        # Invariant violated; skip broadcast.
        return BroadcastResult(client=None, sent_uris=[], error=None)

    mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
    total_posts = len(content_list)
    posted_count = 0
    last_id = None
    sent_ids: List[str] = []
    delivered: List[str] = []

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

    async def _status_post_with_timeout(
        post_text: str,
        reply_to_id: Optional[str],
        media: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ):
        """Post one status, bounded by a timeout, safe to call again on failure.

        The timeout does NOT cancel the request. ``asyncio.to_thread`` runs
        ``status_post`` on a worker thread and Python threads are not cancellable:
        when ``wait_for`` gives up it abandons the *await*, while the HTTP request
        carries on and may well be accepted. ``wait_for`` then raises
        ``TimeoutError``, ``classify_retry`` calls that transient (anything without
        a 429), and ``_send_with_thread_retry`` sends the same status again — a
        real duplicate on the timeline, with the retry's id becoming the tracked
        ``last_id`` while the first post is orphaned outside the reply chain.

        ``idempotency_key`` is Mastodon's own answer to exactly this: repeat calls
        carrying the same key return the original status instead of creating a
        second one. The caller generates one key per thread part and reuses it for
        every retry of that part, so a retry converges on the post that already
        landed rather than adding to it.
        """
        kwargs: Dict[str, Any] = {
            "status": post_text,
            "in_reply_to_id": reply_to_id,
            "visibility": 'public',
        }
        if media:
            kwargs["media_ids"] = media
        if idempotency_key:
            kwargs["idempotency_key"] = idempotency_key
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
            # One key per thread part, bound as a default so every retry of THIS
            # part reuses it while the next part gets its own. A key derived from
            # the text instead would wrongly collapse two legitimately identical
            # posts made at different times.
            post_key = uuid.uuid4().hex

            async def _do_send(pt=post_text, rid=last_id, m=attach, key=post_key):
                return await _status_post_with_timeout(pt, rid, media=m, idempotency_key=key)

            status = await _send_with_thread_retry(_do_send)
            last_id = status.get('id') if isinstance(status, dict) else getattr(status, "id", None)
            if last_id is not None:
                sent_ids.append(str(last_id))
                delivered.append(post_text)
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
        return BroadcastResult(
            client=None, sent_uris=sent_ids, error=e, delivered_texts=delivered
        )

    return BroadcastResult(
        client=None, sent_uris=sent_ids, error=None, delivered_texts=delivered
    )


