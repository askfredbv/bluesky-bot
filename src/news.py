"""News domain logic: fetch and normalise RSS feeds, score items for
relevance (source tier, product/groundbreaking keywords, recency, topic
diversity, cross-publisher consensus), and cluster the same story across
publishers.

Extracted from src/utils.py. Depends on src.net_safety (safe fetch + URL
canonicalisation) and on src.metrics (FeedFetchResult + feed-health);
imports nothing from src.utils.
"""
import asyncio
import calendar
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import feedparser
import httpx

from src.config import (
    CONSENSUS_SYNERGY_BONUS,
    FEED_FETCH_CONCURRENCY,
    FEED_MAX_CONNECTIONS,
    FEED_MAX_KEEPALIVE_CONNECTIONS,
    FEED_REQUEST_CONNECT_TIMEOUT_SECONDS,
    FEED_REQUEST_POOL_TIMEOUT_SECONDS,
    FEED_REQUEST_READ_TIMEOUT_SECONDS,
    FEED_REQUEST_WRITE_TIMEOUT_SECONDS,
    FEED_SUMMARY_MAX_CHARS,
    GROUNDBREAKING_KEYWORDS,
    HIDDEN_GEM_SOURCES,
    LANDMARK_CONSENSUS_MIN_PUBLISHERS,
    LANDMARK_LAUNCH_BONUS,
    MOMENTUM_PRODUCTS,
    MOMENTUM_PRODUCT_BONUS,
    PRODUCT_KEYWORDS,
    RSS_FEEDS,
    SOURCE_TIERS,
    TOPIC_MAP,
)
from src.logger import SafeLogger
from src.metrics import (
    FeedFetchResult,
    check_feed_health_alerts,
    load_feed_health,
    record_feed_attempt,
    save_feed_health,
)
from src.net_safety import canonical_url, get_with_safe_redirects, normalise_url


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


# Flagship names are matched on word boundaries, with an optional dot-number
# version tail ("gemini 3" also matches "gemini 3.8"). A bare substring test lets
# the two-character name "o3" fire inside unrelated identifiers like "o365" or
# "o3de" — harmless when it only nudged the momentum bonus, but entity-level
# consensus counts publishers PER FLAGSHIP, so three outlets covering O365 would
# hand an unrelated product a landmark (Codex #106). One matcher is used for both
# the momentum bonus and the consensus count so they cannot disagree.
_FLAGSHIP_PATTERNS = [
    (p, re.compile(r"\b" + re.escape(p) + r"(?:\.\d+)*\b")) for p in MOMENTUM_PRODUCTS
]


def _flagships_in(text: str) -> List[str]:
    """Named MOMENTUM_PRODUCTS flagships present in `text` (already lowercased)."""
    return [product for product, pattern in _FLAGSHIP_PATTERNS if pattern.search(text)]


def _flagship_family(product: str) -> str:
    """Canonical key grouping aliases and variants of one flagship family.

    Consensus must be counted per FAMILY, not per configured string: publishers
    write the same launch differently, and MOMENTUM_PRODUCTS carries both spelling
    aliases and model variants. Without this, three outlets split across "GPT-5"
    and "GPT 5" score 2/1/2, and "Claude 4" / "Claude Opus 4" / "Claude Sonnet 4"
    score 1/1/1 — neither reaches the threshold, so a broadly covered launch is
    still penalised (Codex #106).

    Derived algorithmically (leading brand token + version digits), NOT from a
    hand-maintained alias table: MOMENTUM_PRODUCTS is auto-refreshed monthly by
    the refresh-momentum workflow, so a manual map would silently drift.
        "gpt-5" / "gpt 5"                                    -> "gpt5"
        "claude 4" / "claude opus 4" / "claude sonnet 4"      -> "claude4"
        "deepseek v4" -> "deepseek4";  "grok 3" -> "grok3" (distinct from grok4)
    """
    tokens = re.findall(r"[a-z]+|\d+", product.lower())
    brand = next((t for t in tokens if t.isalpha()), "")
    digits = "".join(t for t in tokens if t.isdigit())
    return f"{brand}{digits}"


# product string -> family key, resolved once at import.
_FLAGSHIP_FAMILY = {product: _flagship_family(product) for product in MOMENTUM_PRODUCTS}


def annotate_flagship_consensus(items: List[Dict[str, Any]]) -> None:
    """Set item['flagship_publisher_domains']: how many DISTINCT publisher domains
    mention the same named MOMENTUM_PRODUCTS flagship anywhere in the batch.

    ENTITY-level consensus, complementing the STORY-level title clustering above.
    Title clustering needs >= 3 shared significant tokens at Jaccard >= 0.5, which
    punchy launch headlines never reach: "OpenAI launches GPT-5", "GPT-5 is here"
    and "Introducing GPT-5" share only {gpt, 5}, so three independent publishers
    each report a story-level count of 1 and a broadly covered launch would miss
    the landmark gate entirely (Codex #106). Counting publishers per flagship is
    immune to headline wording.

    Deliberately topic-level, not story-level: three outlets writing about GPT-5
    from different angles on the same day is exactly when the topic-diversity
    penalty should not bury it. Mutates in place; feeds the landmark gate only,
    never the consensus-synergy bonus (which stays story-level).
    """
    domains_by_flagship: Dict[str, set] = {}
    matches: List[List[str]] = []
    for item in items:
        text = f"{item.get('title', '')} {item.get('description', '')}".lower()
        domain = _publisher_domain(item.get("link", ""))
        matched = _flagships_in(text)
        matches.append(matched)
        if domain:
            for product in matched:
                domains_by_flagship.setdefault(_FLAGSHIP_FAMILY[product], set()).add(domain)
    for item, matched in zip(items, matches):
        item["flagship_publisher_domains"] = max(
            (len(domains_by_flagship.get(_FLAGSHIP_FAMILY[p], ())) for p in matched),
            default=0,
        )


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

    # 2b. Momentum Product Boost — flagship 2026 models score higher than generic
    # product news. Word-boundary matched (see _flagships_in) so "o3" does not fire
    # inside "o365".
    flagships = _flagships_in(text)
    if flagships: score += MOMENTUM_PRODUCT_BONUS

    # 3. Groundbreaking Tech Boost
    if any(kw in text for kw in GROUNDBREAKING_KEYWORDS): score += 7.0
    
    # 4. Time Decay (Lose 0.5 point per hour)
    age_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
    score -= (age_hours * 0.5)
    
    # Landmark detection (v4.25): a named flagship that MANY INDEPENDENT
    # PUBLISHERS are covering at once. A landmark waives the topic-diversity
    # penalty below and earns LANDMARK_LAUNCH_BONUS, so an obvious flagship story
    # is not buried just because we posted on its topic the run before.
    #
    # Deliberately measured, not parsed. An earlier version tried to detect launch
    # *language* ("X launches GPT-5") with a regex; six Codex review rounds each
    # found a fresh leak (word order, punctuation, decimal versions, mid-word
    # stems like "unreleased", title/description bleed, "o3" inside "o365").
    # Publisher count measures the thing we actually meant — "enough independent
    # outlets think this is news" — and cannot be fooled by wording.
    #
    # Takes the STRONGER of story-level clustering (cross_publisher_domains) and
    # entity-level flagship coverage (flagship_publisher_domains). Story-level
    # alone is not enough: punchy launch headlines share too few title tokens to
    # cluster, so three publishers covering one launch each report 1 (Codex #106).
    # Trade-off accepted: a vendor-only announcement no other outlet has picked up
    # yet gets no landmark. It still scores strongly on source tier + product +
    # momentum, and by the next daily Curator run real launches clear the bar.
    is_momentum = bool(flagships)
    publisher_count = item.get('cross_publisher_domains', 1)
    landmark_publishers = max(publisher_count, item.get('flagship_publisher_domains', 0))
    is_landmark = is_momentum and landmark_publishers >= LANDMARK_CONSENSUS_MIN_PUBLISHERS
    item['is_landmark'] = is_landmark

    # 5. Topic Diversity Penalty — waived for landmarks.
    item_topic = "General"
    for topic, kws in TOPIC_MAP.items():
        if any(kw in text for kw in kws):
            item_topic = topic
            break

    if item_topic in recent_topics and not is_landmark:
        score -= 12.0 # Heavy "Discernment" penalty for repetition

    # 5b. Landmark launch bonus.
    if is_landmark:
        score += LANDMARK_LAUNCH_BONUS

    # 6. Consensus Synergy: reward stories covered by multiple independent
    # sources. feed_count = the same URL across feeds; cross_publisher_domains =
    # the same story across distinct publisher domains (fuzzy title match, set by
    # annotate_cross_publisher_consensus). Take the stronger of the two signals,
    # capped so a very widely-covered story does not dominate the ranking.
    feed_count = len(item.get('source_feeds', []))
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
) -> FeedFetchResult:
    """Fetch and normalise one RSS feed; return a structured outcome.

    The `ok` flag reflects whether the HTTP request succeeded (i.e. no
    transport error). Parse failures flip `ok=False` via the bozo path;
    a feed that responds but has zero entries is `ok=True` with both
    `entries_total` and `entries_accepted` at zero.
    """
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
        SafeLogger.warn("feed_timeout", "Feed request timed out", url=url,
                        error_type=type(e).__name__, error_msg=str(e)[:200])
        return FeedFetchResult(url=url, ok=False, entries_total=0, entries_accepted=0, error_type=type(e).__name__)
    except Exception as e:
        SafeLogger.warn("feed_fetch_failure", "Feed fetch failed", url=url,
                        error_type=type(e).__name__, error_msg=str(e)[:200])
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
    try:
        feed_health = load_feed_health()
        for result in results:
            record_feed_attempt(feed_health, result)
        save_feed_health(feed_health)
        # Surface any feed that has been silently failing across the window
        # (see check_feed_health_alerts) — a dead feed like the removed Anthropic
        # news.rss otherwise stays invisible until someone notices missing coverage.
        # Scope to RSS_FEEDS so a just-removed feed's stale failures don't alert.
        check_feed_health_alerts(feed_health, RSS_FEEDS)
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
    # cover under different URLs, before scoring reads the signal. Story-level
    # (title clustering) feeds the synergy bonus; entity-level (per flagship)
    # feeds the landmark gate, which punchy headlines would otherwise never trip.
    annotate_cross_publisher_consensus(unique_unseen)
    annotate_flagship_consensus(unique_unseen)

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
