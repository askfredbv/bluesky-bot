import re
import random
import asyncio
import httpx
import feedparser
import functools
from typing import List, Dict, Any, Optional, TypeVar
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import io
from PIL import Image
from src.config import (
    RSS_FEEDS, MAX_API_RETRIES, BACKOFF_FACTOR, JITTER_RANGE,
    SOURCE_TIERS, PRODUCT_KEYWORDS, GROUNDBREAKING_KEYWORDS,
    TOPIC_MAP, HIDDEN_GEM_SOURCES,
    FEED_FETCH_CONCURRENCY,
    FEED_REQUEST_CONNECT_TIMEOUT_SECONDS,
    FEED_REQUEST_READ_TIMEOUT_SECONDS,
    FEED_REQUEST_WRITE_TIMEOUT_SECONDS,
    FEED_REQUEST_POOL_TIMEOUT_SECONDS,
    FEED_MAX_CONNECTIONS,
    FEED_MAX_KEEPALIVE_CONNECTIONS,
    FEED_SUMMARY_MAX_CHARS,
    CONSENSUS_SYNERGY_BONUS,
    RATE_LIMIT_BASE_WAIT_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
    GENERIC_IMAGE_PATTERNS,
    MOMENTUM_PRODUCTS,
    MOMENTUM_PRODUCT_BONUS,
)
from src.logger import SafeLogger
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


def classify_retry(exception: Exception) -> str:
    """Return 'rate_limit' for HTTP 429 errors, 'transient' otherwise.

    Duck-typed on `exception.response.status_code` so the classifier works for
    atproto's RequestException and any httpx-style error without importing
    either SDK into utils.py.
    """
    response = getattr(exception, "response", None)
    status = getattr(response, "status_code", None)
    if status == 429:
        return "rate_limit"
    return "transient"


def _parse_retry_after_header(raw: Any) -> Optional[float]:
    """Parse a Bluesky-style `Retry-After` header value.

    Accepts seconds-as-number or an HTTP-date (RFC 7231 §7.1.3). Returns the
    number of seconds to wait, or None if the value cannot be parsed. A
    negative delta (HTTP-date already in the past) is clamped to 0.
    """
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


def _parse_ratelimit_reset_header(raw: Any) -> Optional[float]:
    """Parse a Mastodon-style `X-RateLimit-Reset` header value.

    Accepts either a unix timestamp (seconds-since-epoch) or an ISO-8601
    timestamp. Returns the number of seconds until the reset, or None if the
    value cannot be parsed. Past resets clamp to 0.
    """
    if raw is None:
        return None
    now_ts = datetime.now(timezone.utc).timestamp()
    try:
        reset_ts = float(raw)
        # Plausibility: unix timestamps in 2025+ are ~1.7e9; tiny values
        # are more likely seconds-until-reset than epoch-seconds.
        if reset_ts > 1_000_000_000:
            return max(0.0, reset_ts - now_ts)
        return max(0.0, reset_ts)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def _extract_rate_limit_wait(response: Any) -> Optional[float]:
    """Pull a wait-time (seconds) from response headers. None if unusable.

    Checks, in order:
      - `retry-after` (Bluesky / generic HTTP)
      - `x-ratelimit-reset` (Mastodon)
      - `ratelimit-reset` (legacy fallback)
    """
    headers = getattr(response, "headers", {}) or {}
    # Normalise to lowercase-keyed lookups without assuming dict subtype
    def _get(name: str) -> Any:
        if hasattr(headers, "get"):
            val = headers.get(name)
            if val is not None:
                return val
            return headers.get(name.lower()) or headers.get(name.title())
        return None

    raw_retry_after = _get("retry-after")
    parsed = _parse_retry_after_header(raw_retry_after)
    if parsed is not None:
        return parsed

    raw_reset = _get("x-ratelimit-reset") or _get("ratelimit-reset")
    return _parse_ratelimit_reset_header(raw_reset)


async def sleep_for_rate_limit(attempt: int, exception: Exception, function: str = "unknown") -> None:
    """Sleep appropriately for a 429; raise when the retry budget is exhausted.

    `attempt` is 1-indexed (1 = first retry). Raises the provided exception
    once `attempt > RATE_LIMIT_MAX_RETRIES`.
    """
    if attempt > RATE_LIMIT_MAX_RETRIES:
        SafeLogger.error(
            "rate_limit_exhausted",
            "Rate limit retries exhausted",
            exception=exception,
            attempt=attempt,
            max_attempts=RATE_LIMIT_MAX_RETRIES,
            function=function,
        )
        raise exception

    response = getattr(exception, "response", None)
    header_wait = _extract_rate_limit_wait(response)
    if header_wait is not None:
        wait_time = header_wait
        header_used = True
    else:
        wait_time = float(RATE_LIMIT_BASE_WAIT_SECONDS * attempt)
        header_used = False

    SafeLogger.warn(
        "rate_limit_hit",
        "Rate limit (429); backing off",
        attempt=attempt,
        max_attempts=RATE_LIMIT_MAX_RETRIES,
        wait_seconds=round(wait_time),
        function=function,
        header_used=header_used,
    )
    await asyncio.sleep(wait_time)


async def sleep_for_transient(attempt: int, exception: Exception, function: str = "unknown") -> None:
    """Sleep with exponential backoff for transient errors; raise on exhaustion.

    `attempt` is 1-indexed. Raises the provided exception once
    `attempt > MAX_API_RETRIES`.
    """
    if attempt > MAX_API_RETRIES:
        SafeLogger.error(
            "retry_exhausted",
            "Ultimate failure after retry attempts",
            exception=exception,
            attempt=attempt,
            max_attempts=MAX_API_RETRIES,
            function=function,
        )
        raise exception

    wait_time = (BACKOFF_FACTOR ** attempt) + random.uniform(0, JITTER_RANGE)
    SafeLogger.warn(
        "retry_scheduled",
        "Retry scheduled with backoff",
        attempt=attempt,
        max_attempts=MAX_API_RETRIES,
        function=function,
        wait_seconds=round(wait_time, 2),
    )
    await asyncio.sleep(wait_time)


def retry_with_backoff(func):
    """Decorator to retry an async function, branching by error class.

    Thin wrapper over `classify_retry` + `sleep_for_rate_limit` /
    `sleep_for_transient`. Rate-limit and transient errors get independent
    retry budgets so a rate-limited run doesn't burn its transient budget.
    Total attempts: 1 initial + up-to-MAX_..._RETRIES retries.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        rate_limit_attempts = 0
        transient_attempts = 0
        while True:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if classify_retry(e) == "rate_limit":
                    rate_limit_attempts += 1
                    await sleep_for_rate_limit(rate_limit_attempts, e, function=func.__name__)
                else:
                    transient_attempts += 1
                    await sleep_for_transient(transient_attempts, e, function=func.__name__)
    return wrapper


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

# ── Cross-publisher story clustering (fuzzy consensus) ───────────────────────
# Exact-URL dedup (canonical_url) only catches the same link across feeds. When
# OpenAI, The Verge and TechCrunch all cover "gpt-5 launched" under three
# different URLs, that is a stronger consensus signal than one URL in two feeds,
# yet exact matching misses it. We cluster items by title-token overlap across
# distinct publisher domains and feed the cluster size into the consensus bonus
# — additively, without merging or dropping any item (the Curator still picks
# the single best; near-duplicates competing is fine). Idea from
# strike007-3000/BluBot; here it is a scoring signal, not a merge.
_TITLE_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that", "its", "are", "was",
    "were", "new", "how", "why", "what", "you", "your", "our", "will", "can",
    "has", "have", "into", "over", "out", "about", "just", "now", "not", "but",
})
_CROSS_PUBLISHER_MULTIPLIER_CAP = 4  # widely-covered is strong, not infinite


def _publisher_domain(url: str) -> str:
    """Bare registrable-ish host for a link (lowercased, no leading www.)."""
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _title_tokens(title: str) -> frozenset:
    """Significant lowercased tokens of a headline: words >= 3 chars, plus any
    token containing a digit (version numbers like "5" in GPT-5, "4o", "3.7") so
    distinct model versions do not collapse to the same token set and cluster."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return frozenset(
        w for w in words
        if w not in _TITLE_STOPWORDS and (len(w) >= 3 or any(c.isdigit() for c in w))
    )


def _titles_cluster(a: frozenset, b: frozenset,
                    min_shared: int = 3, min_jaccard: float = 0.5) -> bool:
    """True if two token sets plausibly describe the same story. Conservative on
    purpose: a false cluster wrongly boosts an item, so require both a real
    shared-token count and a high Jaccard ratio."""
    if len(a) < min_shared or len(b) < min_shared:
        return False
    # Distinct version numbers => distinct story (GPT-4 vs GPT-5, Claude 3 vs 4).
    # Restrict the veto to single-digit integer tokens: model/product versions
    # are low integers, whereas funding amounts or years ("$40bn", "40 billion",
    # "2024") must NOT veto an otherwise-strong match. Only blocks when BOTH
    # titles carry a version digit and they don't overlap.
    vers_a = {w for w in a if len(w) == 1 and w.isdigit()}
    vers_b = {w for w in b if len(w) == 1 and w.isdigit()}
    if vers_a and vers_b and vers_a.isdisjoint(vers_b):
        return False
    shared = len(a & b)
    if shared < min_shared:
        return False
    union = len(a | b)
    return union > 0 and (shared / union) >= min_jaccard


def annotate_cross_publisher_consensus(items: List[Dict[str, Any]]) -> None:
    """Set item['cross_publisher_domains']: how many DISTINCT publisher domains
    (including the item's own) carry a title-similar story. Mutates in place.
    Same-domain near-duplicates never inflate the count — only independent
    publishers do."""
    profiles = [(_title_tokens(it.get("title", "")), _publisher_domain(it.get("link", "")))
                for it in items]
    for i, item in enumerate(items):
        tokens_i, domain_i = profiles[i]
        domains = {domain_i} if domain_i else set()
        if len(tokens_i) >= 3:
            for j, (tokens_j, domain_j) in enumerate(profiles):
                if j == i or not domain_j or domain_j == domain_i:
                    continue
                if _titles_cluster(tokens_i, tokens_j):
                    domains.add(domain_j)
        item["cross_publisher_domains"] = max(1, len(domains))


def calculate_relevance_score(item: Dict[str, Any], pub_date: datetime, recent_topics: List[str]) -> float:
    """Calculates a weighted 6-factor score (source tier, product signals, groundbreaking keywords, time decay, topic diversity, consensus synergy)."""
    score = 0.0
    text = f"{item['title']} {item['description']}".lower()
    
    # 1. Source Tier — guard against relative or malformed links
    try:
        parsed = urlparse(item['link'])
        source_domain = parsed.netloc if parsed.netloc else ""
        score += next((val for domain, val in SOURCE_TIERS.items() if domain in source_domain), 3.0)
    except Exception:
        score += 3.0  # Default tier score if link is unparseable
    
    # 2. Product Boost
    if any(kw in text for kw in PRODUCT_KEYWORDS): score += 5.0

    # 2b. Momentum Product Boost — flagship 2026 models score higher than generic product news
    if any(p in text for p in MOMENTUM_PRODUCTS): score += MOMENTUM_PRODUCT_BONUS

    # 3. Groundbreaking Tech Boost
    if any(kw in text for kw in GROUNDBREAKING_KEYWORDS): score += 7.0
    
    # 4. Time Decay (Lose 0.5 point per hour)
    age_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
    score -= (age_hours * 0.5)
    
    # 5. Topic Diversity Penalty
    item_topic = "General"
    for topic, kws in TOPIC_MAP.items():
        if any(kw in text for kw in kws):
            item_topic = topic
            break
    
    if item_topic in recent_topics:
        score -= 12.0 # Heavy "Discernment" penalty for repetition

    # 6. Consensus Synergy: reward stories covered by multiple independent
    # sources. feed_count = the same URL across feeds; cross_publisher_domains =
    # the same story across distinct publisher domains (fuzzy title match, set by
    # annotate_cross_publisher_consensus). Take the stronger of the two signals,
    # capped so a very widely-covered story does not dominate the ranking.
    feed_count = len(item.get('source_feeds', []))
    publisher_count = item.get('cross_publisher_domains', 1)
    consensus_sources = min(max(feed_count, publisher_count),
                            _CROSS_PUBLISHER_MULTIPLIER_CAP + 1)
    if consensus_sources > 1:
        score += CONSENSUS_SYNERGY_BONUS * (consensus_sources - 1)

    item['detected_topic'] = item_topic
    return score

async def fetch_single_feed(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: Optional[httpx.Timeout] = None
) -> "FeedFetchResult":  # noqa: F821 — forward ref, imported lazily inside the function to break the src.metrics ↔ src.utils circular dependency
    """Fetch and normalise one RSS feed; return a structured outcome.

    The `ok` flag reflects whether the HTTP request succeeded (i.e. no
    transport error). Parse failures flip `ok=False` via the bozo path;
    a feed that responds but has zero entries is `ok=True` with both
    `entries_total` and `entries_accepted` at zero.
    """
    # Local import to avoid circular dependency at module load time.
    from src.metrics import FeedFetchResult

    try:
        # SSRF guard: feeds go through the same public-IP validation, DNS
        # pinning, and per-hop redirect checks as the metadata scraper, but
        # skip the metadata domain allowlist (feeds are their own trusted list).
        # A compromised feed origin that 302s to an internal address is blocked.
        response = await get_with_safe_redirects(
            client, url, timeout=timeout, enforce_metadata_policy=False)
        if response is None:
            # get_with_safe_redirects returns None for BOTH a security block and
            # an ordinary transport failure (timeout/TLS/connection), logging the
            # real cause itself. Use a neutral label here so the weekly feed-
            # health view does not mislabel a plain timeout as a security block.
            SafeLogger.warn("feed_fetch_blocked",
                            "Feed fetch failed or was blocked (see prior log for cause)", url=url)
            return FeedFetchResult(url=url, ok=False, entries_total=0,
                                   entries_accepted=0, error_type="FetchFailedOrBlocked")
        feed = feedparser.parse(response.text)
        bozo_error_type: Optional[str] = None
        if feed.bozo:
            bozo_exception = getattr(feed, 'bozo_exception', None)
            bozo_error_type = type(bozo_exception).__name__ if bozo_exception else "UnknownParseError"
            SafeLogger.warn(
                "feed_parse_failure",
                "Feed parse failure",
                url=url,
                error_type=bozo_error_type,
            )

        raw_entries = getattr(feed, 'entries', []) or []
        entries_total = len(raw_entries)

        items: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(days=2)

        for entry in raw_entries:
            time_struct = entry.get('published_parsed') or entry.get('updated_parsed')
            if not time_struct:
                continue

            import calendar
            pub_date = datetime.fromtimestamp(calendar.timegm(time_struct), timezone.utc)
            if pub_date <= lookback:
                continue

            # Normalise the article link — some feeds serve relative URLs
            raw_link = entry.get('link', '').strip()
            normalised_link = normalise_url(raw_link, base_url=url)
            if not normalised_link:
                SafeLogger.warn("feed_entry_skipped_bad_link", "Skipping feed entry with unusable link", raw_link=raw_link)
                continue

            summary = entry.get('summary', entry.get('description', ""))
            clean_summary = re.sub('<[^<]+?>', '', summary)[:FEED_SUMMARY_MAX_CHARS]
            items.append({
                "title": entry.title,
                "description": clean_summary,
                "link": normalised_link,
                "pub_date": pub_date,
                "source_feeds": [url],
            })
        # Request succeeded; bozo-parse still counts as ok=True for feed
        # health because the fetch itself worked — bozo is a soft signal
        # and often transient (malformed <br> tags etc.).
        return FeedFetchResult(
            url=url,
            ok=True,
            entries_total=entries_total,
            entries_accepted=len(items),
            error_type=bozo_error_type,
            entries=items,
        )
    except httpx.TimeoutException as e:
        SafeLogger.warn("feed_timeout", "Feed request timed out", url=url, error_type=type(e).__name__)
        return FeedFetchResult(url=url, ok=False, entries_total=0, entries_accepted=0, error_type=type(e).__name__)
    except Exception as e:
        SafeLogger.warn("feed_fetch_failure", "Feed fetch failed", url=url, error_type=type(e).__name__)
        return FeedFetchResult(url=url, ok=False, entries_total=0, entries_accepted=0, error_type=type(e).__name__)

async def fetch_news(seen_links: List[str], recent_topics: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    """Weighted asynchronous fetch with Hidden Gem injection (v4.5 Sage)."""
    SafeLogger.info("news_fetch_started", "Fetching news from configured feeds", feed_count=len(RSS_FEEDS))

    timeout = httpx.Timeout(
        connect=FEED_REQUEST_CONNECT_TIMEOUT_SECONDS,
        read=FEED_REQUEST_READ_TIMEOUT_SECONDS,
        write=FEED_REQUEST_WRITE_TIMEOUT_SECONDS,
        pool=FEED_REQUEST_POOL_TIMEOUT_SECONDS
    )
    limits = httpx.Limits(
        max_connections=FEED_MAX_CONNECTIONS,
        max_keepalive_connections=FEED_MAX_KEEPALIVE_CONNECTIONS
    )
    semaphore = asyncio.Semaphore(FEED_FETCH_CONCURRENCY)

    async def _fetch_with_semaphore(url: str):
        async with semaphore:
            return await fetch_single_feed(client, url, timeout=timeout)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        limits=limits
    ) as client:
        tasks = [_fetch_with_semaphore(url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks)

    # Record per-feed outcomes for the weekly health view.
    # Local import mirrors fetch_single_feed and keeps the circular-dep
    # resolution consistent.
    try:
        from src.metrics import load_feed_health, record_feed_attempt, save_feed_health
        feed_health = load_feed_health()
        for result in results:
            record_feed_attempt(feed_health, result)
        save_feed_health(feed_health)
    except Exception as e:
        SafeLogger.warn(
            "feed_health_record_failed",
            "Feed health telemetry skipped",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )

    all_raw = [item for result in results for item in result.entries]
    seen: dict = {}
    for item in all_raw:
        key = canonical_url(item['link'])
        if key in seen:
            seen[key]['source_feeds'] = list(set(seen[key]['source_feeds'] + item['source_feeds']))
        else:
            seen[key] = item
    seen_canonical = {canonical_url(link) for link in seen_links}
    unique_unseen = [i for i in seen.values() if canonical_url(i['link']) not in seen_canonical]

    # Cross-publisher consensus: mark stories that multiple distinct publishers
    # cover under different URLs, before scoring reads the signal.
    annotate_cross_publisher_consensus(unique_unseen)

    # Apply Sage Scoring
    for item in unique_unseen:
        item['score'] = calculate_relevance_score(item, item['pub_date'], recent_topics)
    
    ranked = sorted(unique_unseen, key=lambda x: x['score'], reverse=True)
    
    # Hidden Gem Injection (Force at least one arXiv paper if none in top)
    top_candidates = ranked[:limit]
    has_gem = any(any(gem in i['link'] for gem in HIDDEN_GEM_SOURCES) for i in top_candidates)
    
    if not has_gem and len(ranked) > limit:
        for i in range(limit, len(ranked)):
            if any(gem in ranked[i]['link'] for gem in HIDDEN_GEM_SOURCES):
                SafeLogger.info("hidden_gem_injected", "Injecting hidden gem into top candidates", title_preview=ranked[i]['title'][:40])
                top_candidates[-1] = ranked[i]  # Swap last spot for the Gem
                break
                
    return top_candidates
