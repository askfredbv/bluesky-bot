import pytest
from datetime import datetime, timezone, timedelta
from src.utils import calculate_relevance_score

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

def test_topic_diversity_penalty():
    """Verify that recently discussed topics (Topic Memory) get penalized."""
    item = {'title': 'LLM Scaling', 'description': 'GPT news.', 'link': 'https://techcrunch.com/1'}
    now = datetime.now(timezone.utc)
    
    # 1. No penalty
    score_fresh = calculate_relevance_score(item, now, [])
    
    # 2. Penalty (LLM is in recent topics)
    score_penalized = calculate_relevance_score(item, now, ["LLMs"])
    
    assert score_penalized == pytest.approx(score_fresh - 12.0)

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


def test_momentum_product_bonus():
    """Items mentioning flagship 2026 products score MOMENTUM_PRODUCT_BONUS higher."""
    from src.config import MOMENTUM_PRODUCT_BONUS
    # Identical items from same source and age — only difference is product name in title
    base = {'title': 'New AI Model Released', 'description': 'Details', 'link': 'https://techcrunch.com/1'}
    flagship = {'title': 'claude 4 Released by Anthropic', 'description': 'Details', 'link': 'https://techcrunch.com/2'}
    now = datetime.now(timezone.utc)

    score_base = calculate_relevance_score(base, now, [])
    score_flagship = calculate_relevance_score(flagship, now, [])

    assert score_flagship == pytest.approx(score_base + MOMENTUM_PRODUCT_BONUS)


def test_momentum_product_bonus_case_insensitive():
    """Momentum matching is lowercase — title casing should not matter."""
    from src.config import MOMENTUM_PRODUCT_BONUS
    upper_case = {'title': 'GPT-5 Announced', 'description': 'Breaking news.', 'link': 'https://techcrunch.com/1'}
    no_match = {'title': 'New Model Announced', 'description': 'Breaking news.', 'link': 'https://techcrunch.com/2'}
    now = datetime.now(timezone.utc)

    score_match = calculate_relevance_score(upper_case, now, [])
    score_no_match = calculate_relevance_score(no_match, now, [])

    assert score_match == pytest.approx(score_no_match + MOMENTUM_PRODUCT_BONUS)


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
