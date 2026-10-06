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
    MOMENTUM_PRODUCTS,
    MOMENTUM_PRODUCT_BONUS,
    MAX_GEM_CANDIDATES,
    MIN_GEM_CANDIDATES,
    MIN_TIER1_CANDIDATES,
    PRODUCT_KEYWORDS,
    RECENT_TOPICS_WINDOW,
    RSS_FEEDS,
    SOURCE_TIERS,
    TIER1_SOURCE_SCORE,
    TIME_DECAY_MAX,
    TIME_DECAY_MIN_AGE_HOURS,
    TIME_DECAY_PER_HOUR,
    TOPIC_MAP,
    TOPIC_REPEAT_DECAY,
    TOPIC_REPEAT_PENALTY,
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
DEFAULT_SOURCE_TIER = 3.0


def source_tier(link: str) -> float:
    """The SOURCE_TIERS score for a link's host, or DEFAULT_SOURCE_TIER.

    Extracted so scoring and candidate selection cannot disagree about what a
    link's tier is — selection has to recognise a primary source by exactly the
    rule that scored it.

    Matching is on the parsed HOSTNAME, requiring either an exact match or a
    "<something>.<configured domain>" suffix. It used to be a substring test
    against the netloc, which handed OpenAI's tier-10 to `notopenai.com`,
    `openai.com.evil.example` and `openai.com@attacker.example` alike. That was
    always wrong but merely inflated a score; once _is_tier1 began guaranteeing
    shortlist seats it became a way to force a spoofed host into the Curator's
    candidates as an AI lab's own announcement — and items DO arrive from
    arbitrary third-party domains via the Hacker News and Lobsters feeds.
    Using .hostname rather than .netloc also drops userinfo and port, which is
    what defeats the `@attacker.example` form. (Found in review of #169.)

    The suffix rule preserves the documented intent that "openai.com" also
    covers "developers.openai.com". Where several entries match, the most
    specific (longest) wins, so the result no longer depends on dict order.
    """
    try:
        host = (urlparse(link).hostname or "").lower().rstrip(".")
    except Exception:
        return DEFAULT_SOURCE_TIER  # unparseable link
    if not host:
        return DEFAULT_SOURCE_TIER
    matches = [(len(domain), val) for domain, val in SOURCE_TIERS.items()
               if host == domain or host.endswith("." + domain)]
    if not matches:
        return DEFAULT_SOURCE_TIER
    return float(max(matches)[1])


def _is_gem(link: str) -> bool:
    """True for the research/academic sources that get a reserved slot."""
    return any(gem in link for gem in HIDDEN_GEM_SOURCES)


def _is_tier1(link: str) -> bool:
    """True for a primary source: an AI lab's own blog (see TIER1_SOURCE_SCORE)."""
    return source_tier(link) >= TIER1_SOURCE_SCORE


def select_candidates(ranked: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Pick the shortlist handed to the Curator: best-first, but source-mixed.

    Three rules, applied to an already-ranked list:

    * at most MAX_GEM_CANDIDATES research items (arXiv et al), so a nightly
      preprint batch cannot take every slot;
    * at least MIN_GEM_CANDIDATES research item, the original Hidden Gem floor;
    * at least MIN_TIER1_CANDIDATES primary sources when the pool holds them.

    Floors are best-effort and never fabricate: if the pool has no primary
    source, the shortlist simply has none. Promotions evict the lowest-scoring
    item that is not itself needed to satisfy a floor, so the two floors cannot
    starve each other. A promoted item must still out-score nothing-at-all —
    negative scores stay out, since a floor is a diversity guarantee, not a
    licence to ship a bad candidate.
    """
    # The cap may only ever displace a gem in favour of a candidate that is
    # itself worth offering. Applying it across the whole ranked list let a
    # NEGATIVE-scoring non-gem take a slot from a positive gem: gems at
    # 10/9/8/7 plus non-gems at -1/-2/-3 produced [10, 9, -1, -2, -3], which
    # contradicts this function's own contract that a negative score is a bad
    # candidate. So the cap is applied among the positives only.
    # (Found in review of #169.)
    positives = [i for i in ranked if i.get('score', 0.0) > 0]
    remainder = [i for i in ranked if i.get('score', 0.0) <= 0]

    # Pass 1: best-first among the positives, holding the gem cap.
    selected: List[Dict[str, Any]] = []
    gems = 0
    for item in positives:
        if len(selected) >= limit:
            break
        if _is_gem(item['link']):
            if gems >= MAX_GEM_CANDIDATES:
                continue
            gems += 1
        selected.append(item)

    # Pass 2: the cap limits gem REPRESENTATION, it does not shrink the
    # shortlist. If the pool is all research (an arXiv-only morning is the
    # normal case, not the edge case) pass 1 stops at MAX_GEM_CANDIDATES and
    # would hand the model two candidates instead of five. Release the cap and
    # fill from the remaining positives before considering anything weaker.
    chosen = {id(i) for i in selected}
    for item in positives:
        if len(selected) >= limit:
            break
        if id(item) not in chosen:
            selected.append(item)
            chosen.add(id(item))

    # Pass 3: only now, if the pool simply has nothing better, top up from the
    # non-positive remainder. Returning a short list would change what callers
    # downstream can assume, so this preserves "up to `limit`" — but a weak
    # candidate can no longer outrank a good one.
    for item in remainder:
        if len(selected) >= limit:
            break
        if id(item) not in chosen:
            selected.append(item)
            chosen.add(id(item))

    def _promote(predicate, floor: int, event: str) -> None:
        """Top the shortlist up to `floor` items matching `predicate`."""
        chosen_links = {i['link'] for i in selected}
        while sum(1 for i in selected if predicate(i['link'])) < floor:
            candidate = next(
                (i for i in ranked
                 if predicate(i['link'])
                 and i['link'] not in chosen_links
                 and i.get('score', 0.0) > 0),
                None)
            if candidate is None:
                return
            # Evict the weakest item that is not itself holding up a floor.
            evictable = [
                i for i in selected
                if not predicate(i['link'])
                and not (_is_gem(i['link'])
                         and sum(1 for s in selected if _is_gem(s['link'])) <= MIN_GEM_CANDIDATES)
                and not (_is_tier1(i['link'])
                         and sum(1 for s in selected if _is_tier1(s['link'])) <= MIN_TIER1_CANDIDATES)
            ]
            if not evictable:
                return
            victim = min(evictable, key=lambda i: i.get('score', 0.0))
            selected[selected.index(victim)] = candidate
            chosen_links.discard(victim['link'])
            chosen_links.add(candidate['link'])
            SafeLogger.info(event, "Promoted a candidate to balance the shortlist",
                            title_preview=candidate['title'][:40],
                            dropped_preview=victim['title'][:40])

    # Gem floor first: it is the older contract and the narrower pool.
    _promote(_is_gem, MIN_GEM_CANDIDATES, "hidden_gem_injected")
    _promote(_is_tier1, MIN_TIER1_CANDIDATES, "tier1_source_promoted")

    selected.sort(key=lambda i: i.get('score', 0.0), reverse=True)
    return selected


def calculate_relevance_score(item: Dict[str, Any], pub_date: datetime, recent_topics: List[str]) -> float:
    """Calculates a weighted 6-factor score (source tier, product signals, groundbreaking keywords, time decay, topic diversity, consensus synergy)."""
    score = 0.0
    text = f"{item['title']} {item['description']}".lower()
    
    # 1. Source Tier — guard against relative or malformed links
    score += source_tier(item['link'])
    
    # 2. Product Boost
    if any(kw in text for kw in PRODUCT_KEYWORDS): score += 5.0

    # 2b. Momentum Product Boost — flagship 2026 models score higher than generic product news
    if any(p in text for p in MOMENTUM_PRODUCTS): score += MOMENTUM_PRODUCT_BONUS

    # 3. Groundbreaking Tech Boost
    if any(kw in text for kw in GROUNDBREAKING_KEYWORDS): score += 7.0
    
    # 4. Time Decay — bounded, and never a bonus.
    # max(..., 0.0) is load-bearing: a feed carrying a future publication date
    # (timezone bug, scheduled post leaking early) produced a NEGATIVE age, and
    # subtracting a negative number awarded the item points. An entry dated 72h
    # ahead scored 42.0 against a legitimate ceiling near 15 and would have won
    # every run until its timestamp caught up. The lookback filter in
    # fetch_single_feed does not catch it — that only drops items too old.
    age_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
    age_hours = max(age_hours, TIME_DECAY_MIN_AGE_HOURS)
    score -= min(age_hours * TIME_DECAY_PER_HOUR, TIME_DECAY_MAX)
    
    # 5. Topic Repetition Cooldown
    item_topic = "General"
    for topic, kws in TOPIC_MAP.items():
        if any(kw in text for kw in kws):
            item_topic = topic
            break

    # `recent_topics` is ordered oldest -> newest (main.py appends and slices
    # the tail), so the distance of the MOST RECENT occurrence is measured from
    # the end. Distance 0 = posted on the previous run, which costs the full
    # penalty; each run further back costs TOPIC_REPEAT_DECAY as much. Only the
    # most recent occurrence counts: a topic posted three times should go on
    # cooldown once, not accumulate an unpayable debt (the flat -12.0 this
    # replaced effectively did the latter, because the topic never left the
    # list). See TOPIC_REPEAT_PENALTY in config.py for the full history.
    window = recent_topics[-RECENT_TOPICS_WINDOW:] if recent_topics else []
    for distance, topic in enumerate(reversed(window)):
        if topic == item_topic:
            score -= TOPIC_REPEAT_PENALTY * (TOPIC_REPEAT_DECAY ** distance)
            break

    # 6. Consensus Synergy: reward stories covered by multiple independent
    # sources. feed_count = the same URL across feeds; cross_publisher_domains =
    # the same story across distinct publisher domains (fuzzy title match, set by
    # annotate_cross_publisher_consensus). Take the stronger of the two signals,
    # capped so a very widely-covered story does not dominate the ranking.
    #
    # feed_count counts distinct feed HOSTS, not raw feed URLs: one publisher can
    # emit the same article on several of its own category feeds (blog.google
    # ships it on both technology/ai and products/gemini), which is not
    # independent corroboration and was quietly awarding a consensus bonus.
    feed_count = len({_publisher_domain(f) for f in item.get('source_feeds', []) if f})
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
        # A non-2xx response is a failed fetch, not a successful one that happens
        # to contain an error page. Without this check a feed returning 404 or 500
        # still counted as ok=True (the HTML body just parses to zero entries), so
        # last_ok_at refreshed every run and the "broken" health gate could never
        # fire — a dead feed would surface only as "stale" days later, or not at
        # all if the error page carried parseable entries.
        if not (200 <= response.status_code < 300):
            SafeLogger.warn("feed_fetch_http_error",
                            "Feed returned a non-success HTTP status",
                            url=url, status_code=response.status_code)
            return FeedFetchResult(url=url, ok=False, entries_total=0,
                                   entries_accepted=0,
                                   error_type=f"HTTP{response.status_code}")
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
        # Surface any feed that has been silently failing across the window (see
        # check_feed_health_alerts) — a dead feed like the removed Anthropic
        # news.rss otherwise stays invisible until someone notices the gap in
        # coverage. Scoped to RSS_FEEDS so a just-removed feed's stale failure
        # window does not alert forever.
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

    # Source-mixed shortlist: gem floor AND cap, plus a primary-source floor.
    # See select_candidates — this replaced a bare ranked[:limit] whose only
    # correction was to guarantee arXiv a seat, with nothing guaranteeing one to
    # a primary source.
    return select_candidates(ranked, limit)
