import pytest
from datetime import datetime, timezone, timedelta
from src.config import (
    MOMENTUM_PRODUCTS,
    MOMENTUM_PRODUCT_BONUS,
    RECENT_TOPICS_WINDOW,
    SOURCE_TIERS,
    TOPIC_REPEAT_DECAY,
    TOPIC_REPEAT_PENALTY,
)
from src.news import calculate_relevance_score

def test_source_tier_ranking():
    """Verify that elite sources (OpenAI) get higher scores than general news."""
    item_openai = {'title': 'OpenAI Update', 'description': 'News', 'link': 'https://openai.com/1'}
    item_general = {'title': 'General Tech', 'description': 'News', 'link': 'https://techcrunch.com/1'}
    
    now = datetime.now(timezone.utc)
    score_openai = calculate_relevance_score(item_openai, now, [])
    score_general = calculate_relevance_score(item_general, now, [])
    
    assert score_openai > score_general

def test_groundbreaking_boost():
    """Verify that 'breakthrough' keywords significantly boost the score."""
    item_normal = {'title': 'AI Tool', 'description': 'An app.', 'link': 'https://techcrunch.com/1'}
    item_frontier = {'title': 'Frontier Model Breakthrough', 'description': 'SOTA benchmark scaling.', 'link': 'https://techcrunch.com/2'}
    
    now = datetime.now(timezone.utc)
    score_normal = calculate_relevance_score(item_normal, now, [])
    score_frontier = calculate_relevance_score(item_frontier, now, [])
    
    assert score_frontier > score_normal # The groundbreaking boost (+7) should beat the product boost (+5)

LLM_ITEM = {'title': 'LLM Scaling', 'description': 'GPT news.',
            'link': 'https://techcrunch.com/1'}


def test_topic_repeat_cooldown_full_penalty_for_last_posted():
    """A topic posted on the previous run costs the full cooldown."""
    now = datetime.now(timezone.utc)
    score_fresh = calculate_relevance_score(dict(LLM_ITEM), now, [])
    score_penalised = calculate_relevance_score(dict(LLM_ITEM), now, ["LLMs"])

    assert score_penalised == pytest.approx(score_fresh - TOPIC_REPEAT_PENALTY)


def test_topic_repeat_cooldown_decays_with_distance():
    """The further back the topic was posted, the smaller the penalty.

    This is the behaviour the flat -12.0 lacked: a topic touched once sat in
    `recent_topics` at full strength until five further non-General posts
    pushed it out, which at the real posting rate meant months.
    """
    now = datetime.now(timezone.utc)
    fresh = calculate_relevance_score(dict(LLM_ITEM), now, [])

    # "LLMs" posted 1, 2 and 3 runs before the most recent post.
    penalties = []
    for distance in range(3):
        window = ["LLMs"] + ["General"] * distance
        scored = calculate_relevance_score(dict(LLM_ITEM), now, window)
        penalties.append(fresh - scored)
        assert penalties[-1] == pytest.approx(
            TOPIC_REPEAT_PENALTY * (TOPIC_REPEAT_DECAY ** distance))

    # Strictly decreasing — never flat, never growing.
    assert penalties[0] > penalties[1] > penalties[2] > 0


def test_topic_repeat_cooldown_counts_only_most_recent_occurrence():
    """A topic repeated in the window is charged once, at its nearest distance.

    Summing every occurrence would let a topic accrue a debt it can never pay
    off, which is the trap the previous implementation fell into in production.
    """
    now = datetime.now(timezone.utc)
    fresh = calculate_relevance_score(dict(LLM_ITEM), now, [])
    # The live 2026-10-06 window shape: "LLMs" appears twice, most recently last.
    window = ["LLMs", "Vision/Robot", "Policy/Society", "Compute/HW", "LLMs"]
    scored = calculate_relevance_score(dict(LLM_ITEM), now, window)

    assert fresh - scored == pytest.approx(TOPIC_REPEAT_PENALTY)


def test_topic_repeat_cooldown_expires_outside_window():
    """Beyond RECENT_TOPICS_WINDOW posts ago, a topic costs nothing at all."""
    now = datetime.now(timezone.utc)
    fresh = calculate_relevance_score(dict(LLM_ITEM), now, [])
    aged_out = ["LLMs"] + ["General"] * RECENT_TOPICS_WINDOW

    assert calculate_relevance_score(dict(LLM_ITEM), now, aged_out) == pytest.approx(fresh)


def test_topic_repeat_cooldown_never_outweighs_the_source_tier_spread():
    """The cooldown must not outrank source quality.

    The tier spread is 3.0 (unknown blog) to 10.0 (primary source). The old
    -12.0 exceeded it, so a repeated topic from a primary source lost to an
    unknown blog on topic alone. A diversity signal may break a tie; it may not
    overrule where the story came from.
    """
    assert TOPIC_REPEAT_PENALTY < max(SOURCE_TIERS.values()) - 3.0


def test_general_topic_is_penalised_like_any_other():
    """"General" is a topic on cooldown too, not an exempt catch-all.

    75% of candidate slots over three weeks were "General" arXiv preprints. If
    "General" were exempt the cooldown would penalise only the on-brand news
    categories and leave the dominant flood untouched — which is exactly what
    production did.
    """
    plain = {'title': 'Assorted updates', 'description': 'Notes.',
             'link': 'https://example.com/1'}
    now = datetime.now(timezone.utc)

    probe = dict(plain)
    fresh = calculate_relevance_score(probe, now, [])
    assert probe['detected_topic'] == "General", "test premise: item matches no topic"

    scored = calculate_relevance_score(dict(plain), now, ["General"])
    assert fresh - scored == pytest.approx(TOPIC_REPEAT_PENALTY)

def test_time_decay():
    """Verify that older articles lose points over time."""
    item = {'title': 'News', 'description': 'Description', 'link': 'https://techcrunch.com/1'}
    now = datetime.now(timezone.utc)
    older = now - timedelta(hours=10)

    score_new = calculate_relevance_score(item, now, [])
    score_old = calculate_relevance_score(item, older, [])

    assert score_new > score_old


def test_consensus_synergy_single_feed_no_bonus():
    """An item from a single feed should receive no Consensus Synergy bonus."""
    item = {'title': 'AI News', 'description': 'Details', 'link': 'https://techcrunch.com/1', 'source_feeds': ['https://feed-a.com/rss']}
    now = datetime.now(timezone.utc)
    score_with_one = calculate_relevance_score(item, now, [])

    item_no_feeds = {'title': 'AI News', 'description': 'Details', 'link': 'https://techcrunch.com/1'}
    score_without = calculate_relevance_score(item_no_feeds, now, [])

    assert score_with_one == pytest.approx(score_without)


def test_consensus_synergy_two_feeds_adds_bonus():
    """An item covered by two feeds should get +1.5 over the single-feed baseline."""
    from src.config import CONSENSUS_SYNERGY_BONUS
    item_one = {'title': 'AI News', 'description': 'Details', 'link': 'https://techcrunch.com/1', 'source_feeds': ['https://feed-a.com/rss']}
    item_two = {'title': 'AI News', 'description': 'Details', 'link': 'https://techcrunch.com/1', 'source_feeds': ['https://feed-a.com/rss', 'https://feed-b.com/rss']}
    now = datetime.now(timezone.utc)

    score_one = calculate_relevance_score(item_one, now, [])
    score_two = calculate_relevance_score(item_two, now, [])

    assert score_two == pytest.approx(score_one + CONSENSUS_SYNERGY_BONUS)


def test_consensus_synergy_three_feeds_adds_double_bonus():
    """An item covered by three feeds should get +3.0 (2 * CONSENSUS_SYNERGY_BONUS)."""
    from src.config import CONSENSUS_SYNERGY_BONUS
    item_one = {'title': 'AI News', 'description': 'Details', 'link': 'https://techcrunch.com/1', 'source_feeds': ['https://feed-a.com/rss']}
    item_three = {'title': 'AI News', 'description': 'Details', 'link': 'https://techcrunch.com/1', 'source_feeds': ['https://feed-a.com/rss', 'https://feed-b.com/rss', 'https://feed-c.com/rss']}
    now = datetime.now(timezone.utc)

    score_one = calculate_relevance_score(item_one, now, [])
    score_three = calculate_relevance_score(item_three, now, [])

    assert score_three == pytest.approx(score_one + 2 * CONSENSUS_SYNERGY_BONUS)


@pytest.mark.parametrize("product", MOMENTUM_PRODUCTS)
def test_momentum_product_bonus(product):
    """EVERY configured flagship name must earn MOMENTUM_PRODUCT_BONUS.

    Parametrised over the live list rather than hardcoding a name: the two
    tests this replaced asserted "claude 4" and "GPT-5", so they kept passing
    while the list itself went a generation stale and the bonus stopped firing
    on real launches (Gemini 4 Argon, 2026-09-30). A configured name that
    cannot match is now a test failure, not a silent dead entry.
    """
    # Identical items from the same source and age — the only difference is the
    # product name in the title.
    base = {'title': 'New AI Model Released', 'description': 'Details',
            'link': 'https://techcrunch.com/1'}
    flagship = {'title': f'{product} Released by a Lab', 'description': 'Details',
                'link': 'https://techcrunch.com/2'}
    now = datetime.now(timezone.utc)

    score_base = calculate_relevance_score(base, now, [])
    score_flagship = calculate_relevance_score(flagship, now, [])

    assert score_flagship == pytest.approx(score_base + MOMENTUM_PRODUCT_BONUS)


@pytest.mark.parametrize("product", MOMENTUM_PRODUCTS)
def test_momentum_product_bonus_case_insensitive(product):
    """Momentum matching is lowercase — title casing must not matter."""
    upper_case = {'title': f'{product.upper()} Announced', 'description': 'Breaking news.',
                  'link': 'https://techcrunch.com/1'}
    no_match = {'title': 'New Model Announced', 'description': 'Breaking news.',
                'link': 'https://techcrunch.com/2'}
    now = datetime.now(timezone.utc)

    score_match = calculate_relevance_score(upper_case, now, [])
    score_no_match = calculate_relevance_score(no_match, now, [])

    assert score_match == pytest.approx(score_no_match + MOMENTUM_PRODUCT_BONUS)


def test_momentum_products_are_substring_safe():
    """No flagship name may be a bare word that matches unrelated copy.

    Matching is a substring test over title+description, so an unqualified
    product word quietly boosts noise: "beam" hits Google Beam (video calling)
    as readily as Reflection's Beam model, and "muse" hits "museum". Require
    every entry to carry a version or qualifier: a digit ("gemini 4"), a second
    word ("north 2"), or a hyphenated compound ("gpt-oss"). A single bare
    dictionary word is rejected.
    """
    for product in MOMENTUM_PRODUCTS:
        qualified = (any(c.isdigit() for c in product)
                     or " " in product
                     or "-" in product)
        assert qualified, (
            f"{product!r} is an unqualified bare word; add a version or qualifier"
        )


def test_fetch_news_merges_source_feeds_on_duplicate_link(monkeypatch):
    """Same link from different feeds must merge into one item carrying both
    feeds. Exercises the real fetch_news dedup path (not a reimplementation)."""
    import asyncio

    from src import news
    from src.metrics import FeedFetchResult

    now = datetime.now(timezone.utc)
    item_a = {'title': 'Shared Story', 'description': 'Details', 'link': 'https://example.com/story', 'source_feeds': ['https://feed-a.com/rss'], 'pub_date': now}
    item_b = {'title': 'Shared Story', 'description': 'Details', 'link': 'https://example.com/story', 'source_feeds': ['https://feed-b.com/rss'], 'pub_date': now}
    item_c = {'title': 'Unique Story', 'description': 'Details', 'link': 'https://example.com/other', 'source_feeds': ['https://feed-a.com/rss'], 'pub_date': now}

    async def _fake_fetch_single_feed(client, url, *, timeout=None):
        return FeedFetchResult(url=url, ok=True, entries_total=3,
                               entries_accepted=3, entries=[item_a, item_b, item_c])

    # one feed so the mock's items are collected once; keep the test offline
    monkeypatch.setattr(news, "RSS_FEEDS", ["https://feed-a.com/rss"])
    monkeypatch.setattr(news, "fetch_single_feed", _fake_fetch_single_feed)
    monkeypatch.setattr(news, "load_feed_health", lambda: {})
    monkeypatch.setattr(news, "record_feed_attempt", lambda *a, **k: None)
    monkeypatch.setattr(news, "save_feed_health", lambda *a, **k: None)

    result = asyncio.run(news.fetch_news(seen_links=[], recent_topics=[]))

    links = {i['link'] for i in result}
    assert links == {'https://example.com/story', 'https://example.com/other'}
    shared = next(i for i in result if i['link'] == 'https://example.com/story')
    assert set(shared['source_feeds']) == {'https://feed-a.com/rss', 'https://feed-b.com/rss'}


def test_consensus_ignores_same_publisher_category_feeds():
    """One publisher emitting an article on several of its OWN category feeds is
    not independent corroboration and must not earn a consensus bonus.

    blog.google ships the same Gemini post on both technology/ai and
    products/gemini; counting raw feed URLs awarded +1.5 for that (Codex #106).
    """
    from src.config import CONSENSUS_SYNERGY_BONUS
    now = datetime.now(timezone.utc)

    def score(feeds):
        return calculate_relevance_score(
            {'title': 'Gemini update', 'description': 'x',
             'link': 'https://blog.google/a', 'source_feeds': feeds}, now, [])

    one_google = score(['https://blog.google/technology/ai/rss/'])
    both_google = score(['https://blog.google/technology/ai/rss/',
                         'https://blog.google/products/gemini/rss/'])
    two_publishers = score(['https://blog.google/technology/ai/rss/',
                            'https://techcrunch.com/feed/'])

    assert both_google == pytest.approx(one_google)          # same publisher: no bonus
    assert two_publishers == pytest.approx(one_google + CONSENSUS_SYNERGY_BONUS)
