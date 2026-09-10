import httpx
from typing import Dict, Any, TypeVar
from bs4 import BeautifulSoup
import io
from PIL import Image
from src.config import (
    GENERIC_IMAGE_PATTERNS,
)
from src.logger import SafeLogger
# Backward-compat re-export: news domain logic moved to src.news.
from src.news import (  # noqa: F401
    _publisher_domain,
    _title_tokens,
    _titles_cluster,
    annotate_cross_publisher_consensus,
    calculate_relevance_score,
    fetch_single_feed,
    fetch_news,
)
# Backward-compat re-export: retry/backoff moved to src.retry.
from src.retry import (  # noqa: F401
    classify_retry,
    _parse_retry_after_header,
    _parse_ratelimit_reset_header,
    _extract_rate_limit_wait,
    sleep_for_rate_limit,
    sleep_for_transient,
    retry_with_backoff,
)
# Backward-compat re-export: SSRF/URL-safety moved to src.net_safety.
from src.net_safety import (  # noqa: F401
    canonical_url,
    normalise_url,
    is_safe_public_url,
    _is_public_ip,
    _resolve_public_ip_candidates,
    _hostname_matches_policy,
    is_allowed_metadata_fetch_url,
    _resolver_pinned_to_ips,
    get_with_safe_redirects,
    _resolver_pin_lock,
)
# Backward-compat re-export: state IO moved to src.state_store; these names
# stay importable from src.utils for existing callers.
from src.state_store import (  # noqa: F401
    _state_store_url_for_key,
    _state_store_headers,
    _load_state_from_store,
    _save_state_to_store,
    _load_gist_state_strict,
    _load_gist_state,
    _save_gist_state,
    _atomic_write_json,
    _load_json_with_repair,
    _file_lock,
    _ensure_pioneer_field,
    prune_pioneer_recent,
    load_seen_articles,
    save_seen_articles,
    load_replied_to,
    save_replied_to,
    update_seen_articles,
    update_replied_to,
    STATE_STORE_TIMEOUT_SECONDS,
)

# Decompression-bomb guard (process-wide Pillow setting). The bot opens remote,
# attacker-influenceable images via Pillow — OpenGraph thumbnails from article
# URLs and generated post images. A malicious feed could serve a tiny file that
# declares enormous dimensions; without a cap, decoding it OOMs the runner.
# Pillow's default (~89M px) is generous for a social bot; 10M px comfortably
# covers any legitimate post image and blocks the absurd sizes. Pillow raises
# DecompressionBombError at open() from the header dimensions, which the guarded
# Image.open call sites already catch.
Image.MAX_IMAGE_PIXELS = 10_000_000

T = TypeVar("T")


def compress_image_to_fit(image_bytes: bytes, max_bytes: int) -> tuple[bytes, bool]:
    """Re-encode (and if needed downscale) an image to fit under ``max_bytes``.

    The ONE image compressor. Returns ``(bytes, fits)``. Already-small images
    pass through unchanged. For the rest: re-encode to JPEG at descending
    quality, then progressively downscale, until the result is under budget,
    returning the first that fits. If nothing fits, or the bytes cannot be
    decoded at all, returns the original bytes with ``fits=False`` so the caller
    can skip the attach. Never raises.

    History: written 2026-06-14 in broadcasters.py for the Bluesky image embed,
    because the image model returns 1:1 PNGs around or above the 976 KB blob
    gate and the broadcaster used to measure-and-drop them. A second, weaker
    compressor lived here as ``compress_image`` and served the Curator fallback
    image and publisher og:images. It never downscaled, so it met budgets by
    crushing JPEG quality toward 10; it re-encoded images that were already
    small; and it converted only RGBA/P, so an LA image could not be encoded at
    all. Moved here 2026-09-10 (freeze audit C3) so both paths share this one.
    """
    if len(image_bytes) <= max_bytes:
        return image_bytes, True
    try:
        img: Image.Image = Image.open(io.BytesIO(image_bytes))
        # JPEG has no alpha: flatten anything that is not already RGB or L
        # (RGBA, P, LA, CMYK ...). The old compress_image converted only RGBA
        # and P, so an LA image could not be encoded at all.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        for scale in (1.0, 0.85, 0.7, 0.55, 0.4):
            if scale == 1.0:
                candidate = img
            else:
                w, h = img.size
                candidate = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            for quality in (85, 75, 60):
                buf = io.BytesIO()
                candidate.save(buf, format="JPEG", quality=quality, optimize=True)
                data = buf.getvalue()
                if len(data) <= max_bytes:
                    return data, True
        # Genuinely tried everything and nothing fit: a real "too large".
        return image_bytes, False
    except Exception as e:
        # A real compression FAILURE (unsupported format, truncated bytes, a
        # decompression bomb, an encoder regression), distinct from "too large".
        # Capture the reason so an image outage is diagnosable (error_msg
        # discipline, Codex review on PR #56).
        SafeLogger.warn(
            "image_compress_failed",
            "Could not re-encode oversized image; returning the original bytes",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        return image_bytes, False


def compress_image(image_bytes: bytes, max_size_kb: int = 900) -> bytes:
    """Compress an image to stay under the ~1 MB AtProto blob limit.

    A thin wrapper over ``compress_image_to_fit`` for callers that only want the
    bytes. Never raises: returns the ORIGINAL bytes when the image cannot be
    decoded or shrunk, which is the contract ``is_usable_image`` documents and
    every caller relies on, so callers must still validate the result.
    """
    data, _fits = compress_image_to_fit(image_bytes, max_size_kb * 1024)
    return data


def is_usable_image(image_bytes: bytes, max_bytes: int = 976 * 1024) -> bool:
    """True if bytes decode as an image and fit Bluesky's blob limit. Needed
    because compress_image returns the ORIGINAL bytes when Pillow cannot open or
    shrink them, so callers must validate the result before shipping it as media."""
    if not image_bytes or len(image_bytes) > max_bytes:
        return False
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
        return True
    except Exception:
        return False


async def get_link_metadata(url: str) -> Dict[str, Any]:
    """Scrapes OpenGraph metadata from a URL (v4.5 Sage replacement for DALL-E)."""
    fallback = {"title": "Source Link", "description": "", "image_data": None, "url": url}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    if not is_safe_public_url(url):
        SafeLogger.warn("unsafe_article_url_blocked", "Blocked unsafe article URL", url=url)
        return fallback
    if not is_allowed_metadata_fetch_url(url):
        return fallback

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await get_with_safe_redirects(client, url, headers=headers, timeout=10.0)
            if response is None:
                return fallback
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            og_title = soup.find("meta", property="og:title")
            og_description = soup.find("meta", property="og:description")
            og_image = soup.find("meta", property="og:image")
            
            # Download image if exists for uploading as blob
            img_data = None
            if og_image and og_image.get('content'):
                img_url = normalise_url(str(og_image['content']), base_url=url)
                if img_url:
                    if not is_safe_public_url(img_url):
                        SafeLogger.warn("unsafe_og_image_url_blocked", "Blocked unsafe og:image URL", url=img_url)
                    elif not is_allowed_metadata_fetch_url(img_url):
                        SafeLogger.warn("domain_policy_blocked", "Blocked og:image by metadata domain policy", url=img_url)
                    elif any(p in img_url.lower() for p in GENERIC_IMAGE_PATTERNS):
                        SafeLogger.info("generic_logo_skipped", "Skipping generic logo thumbnail", url=img_url)
                    else:
                        # The thumbnail is an enrichment; the headline and the
                        # description are the payload. Isolate the image work so a
                        # slow host, a dropped connection or an undecodable file
                        # costs the card its picture and not its title — the
                        # function-wide handler below returns the "Source Link"
                        # fallback, which is the wrong answer for an article whose
                        # OpenGraph tags parsed perfectly well.
                        try:
                            img_res = await get_with_safe_redirects(client, img_url, timeout=5.0)
                            if img_res and img_res.status_code == 200:
                                img_data = img_res.content
                                if len(img_data) > 900 * 1024:
                                    SafeLogger.info("og_image_compression_started", "Compressing large OpenGraph image", size_kb=len(img_data)//1024)
                                    img_data = compress_image(img_data)
                            # Validate before the bytes can reach upload_blob, the same check the
                            # generated fallback image already gets (main.py). An undecodable or
                            # still-oversized thumbnail used to go through unchecked: it failed at
                            # upload, and being non-empty it also stopped the Curator's generated
                            # fallback image from firing, so the card shipped with no picture.
                            if img_data is not None and not is_usable_image(img_data):
                                SafeLogger.info(
                                    "og_image_unusable",
                                    "OpenGraph thumbnail did not validate; dropping it so the fallback can run",
                                    url=img_url,
                                    size_bytes=len(img_data),
                                )
                                img_data = None
                        except Exception as e:
                            SafeLogger.warn(
                                "og_image_fetch_failed",
                                "OpenGraph thumbnail could not be fetched; keeping the article metadata",
                                error_type=type(e).__name__,
                                error_msg=str(e)[:200],
                                url=img_url,
                            )
                            img_data = None

            return {
                "title": og_title['content'] if og_title else soup.title.string if soup.title else "Technical Insight",
                "description": og_description['content'][:200] if og_description else "",
                "image_data": img_data,
                "url": url
            }
    except Exception as e:
        SafeLogger.error("metadata_extraction_failed", "Metadata extraction failed", exception=e, url=url)
        return fallback


