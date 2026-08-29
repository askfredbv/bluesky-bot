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


def compress_image(image_bytes: bytes, max_size_kb: int = 900) -> bytes:
    """Compresses an image to stay under AtProto's 1MB blob limit."""
    try:
        img_io = io.BytesIO(image_bytes)
        img = Image.open(img_io)
    except Exception as e:
        SafeLogger.warn("image_open_for_compression_failed", "Failed to open image for compression", error_type=type(e).__name__)
        return image_bytes

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    quality = 90
    out_io = io.BytesIO()
    img.save(out_io, format="JPEG", quality=quality)
    
    while out_io.tell() > max_size_kb * 1024 and quality > 10:
        quality -= 10
        out_io = io.BytesIO()
        img.save(out_io, format="JPEG", quality=quality)

    return out_io.getvalue()


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
                img_url = normalise_url(og_image['content'], base_url=url)
                if img_url:
                    if not is_safe_public_url(img_url):
                        SafeLogger.warn("unsafe_og_image_url_blocked", "Blocked unsafe og:image URL", url=img_url)
                    elif not is_allowed_metadata_fetch_url(img_url):
                        SafeLogger.warn("domain_policy_blocked", "Blocked og:image by metadata domain policy", url=img_url)
                    elif any(p in img_url.lower() for p in GENERIC_IMAGE_PATTERNS):
                        SafeLogger.info("generic_logo_skipped", "Skipping generic logo thumbnail", url=img_url)
                    else:
                        img_res = await get_with_safe_redirects(client, img_url, timeout=5.0)
                        if img_res and img_res.status_code == 200:
                            img_data = img_res.content
                            if len(img_data) > 900 * 1024:
                                SafeLogger.info("og_image_compression_started", "Compressing large OpenGraph image", size_kb=len(img_data)//1024)
                                img_data = compress_image(img_data)

            return {
                "title": og_title['content'] if og_title else soup.title.string if soup.title else "Technical Insight",
                "description": og_description['content'][:200] if og_description else "",
                "image_data": img_data,
                "url": url
            }
    except Exception as e:
        SafeLogger.error("metadata_extraction_failed", "Metadata extraction failed", exception=e, url=url)
        return fallback


