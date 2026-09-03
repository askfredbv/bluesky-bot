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
    LAUNCH_SIGNAL_KEYWORDS,
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
# Landmark launch *construction*: a launch verb governing a named flagship,
# within a few words, in either order — so the launch is about the flagship, not
# an unrelated launch that merely mentions it as context. Raw character distance
# was insufficient (Codex #106): "Acme launches a migration tool ... from GPT-5"
# has the words near each other but GPT-5 is the migration source, not the thing
# launched. Requiring launch-verb → flagship (or flagship → launch-verb) with at
# most a few intervening words captures "OpenAI launches GPT-5" / "GPT-5 is now
# available" while rejecting that shape.
#
# Built once at import from the config lists:
#   - launch stems get a \w* tail so "launch" matches "launches"/"launched";
#   - flagship names get a [\w.]* tail so "gemini 3" matches "gemini 3.8"/"...-pro";
#   - the gap allows up to 3 intervening WORDS (lazy) on either side, and the
#     separators are \W (any non-word char) rather than only whitespace, so
#     punctuation between the verb and the flagship is tolerated — "Introducing:
#     GPT-5" and "GPT-5, released today" match (Codex #106).
# The match is applied per CLAUSE (text is split on sentence terminators first),
# so a launch in the next sentence cannot reach back to a flagship in this one —
# "GPT-5 tops the charts. A startup released a toaster." is not a landmark.
_LANDMARK_MAX_GAP_WORDS: int = 3
_LAUNCH_ALT = "|".join(re.escape(k) + r"\w*" for k in LAUNCH_SIGNAL_KEYWORDS)
_FLAGSHIP_ALT = "|".join(re.escape(p) + r"[\w.]*" for p in MOMENTUM_PRODUCTS)
_LANDMARK_GAP = r"(?:\W+\w+){0,%d}?\W+" % _LANDMARK_MAX_GAP_WORDS
_FLAGSHIP_LAUNCH_RE = re.compile(
    rf"(?:{_LAUNCH_ALT}){_LANDMARK_GAP}(?:{_FLAGSHIP_ALT})"
    rf"|(?:{_FLAGSHIP_ALT}){_LANDMARK_GAP}(?:{_LAUNCH_ALT})"
)
# Clause boundaries: sentence terminators. Deliberately NOT comma/colon — those
# are intra-clause connectors ("Introducing: GPT-5", "GPT-5, released").
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;]+")


def _flagship_launch_nearby(text: str) -> bool:
    """True if any clause of `text` (already lowercased) contains a launch
    construction about a named flagship — a launch verb governing a
    MOMENTUM_PRODUCTS flagship within a few words, in either order.

    Ties the launch to the flagship so an unrelated launch that merely mentions
    the flagship does not manufacture a landmark (Codex #106): "OpenAI launches
    GPT-5", "GPT-5 is now available", "Introducing: GPT-5" qualify; "Acme launches
    a migration tool ... from GPT-5" and a launch in a separate sentence do not.
    """
    return any(_FLAGSHIP_LAUNCH_RE.search(clause) for clause in _CLAUSE_SPLIT_RE.split(text))


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
    
    # Landmark detection (v4.25): a flagship launch overrides the repetition
    # penalty below and earns a bonus. Anchored to a named momentum product so a
    # routine topic repeat never qualifies. Two shapes qualify:
    #   - the launch signal sits NEAR the flagship (_flagship_launch_nearby), so
    #     "OpenAI launches GPT-5" fires but "GPT-5 is great; Acme launches a
    #     toaster" does not — the launch must be about the flagship (Codex #106);
    #   - the flagship is named AND many independent publishers cover it at once
    #     (a big flagship story even without an explicit launch word).
    is_momentum = any(p in text for p in MOMENTUM_PRODUCTS)
    publisher_count = item.get('cross_publisher_domains', 1)
    is_landmark = _flagship_launch_nearby(text) or (
        is_momentum and publisher_count >= LANDMARK_CONSENSUS_MIN_PUBLISHERS
    )
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
