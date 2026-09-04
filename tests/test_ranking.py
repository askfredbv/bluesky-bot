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
    # Identical items from same source and age — only difference is the product
    # name in the title. Single publisher on both sides, so neither is a landmark
    # (v4.25 landmarks need flagship + publisher consensus) and the delta is the
    # momentum bonus alone.
    base = {'title': 'New AI Model', 'description': 'Details', 'link': 'https://techcrunch.com/1'}
    flagship = {'title': 'claude 4 by Anthropic', 'description': 'Details', 'link': 'https://techcrunch.com/2'}
    now = datetime.now(timezone.utc)

    score_base = calculate_relevance_score(base, now, [])
    score_flagship = calculate_relevance_score(flagship, now, [])

    assert score_flagship == pytest.approx(score_base + MOMENTUM_PRODUCT_BONUS)


def test_momentum_product_bonus_case_insensitive():
    """Momentum matching is lowercase — title casing should not matter."""
    from src.config import MOMENTUM_PRODUCT_BONUS
    # Single publisher on both sides, so the landmark gate never fires (see above).
    upper_case = {'title': 'GPT-5 Update', 'description': 'Breaking news.', 'link': 'https://techcrunch.com/1'}
    no_match = {'title': 'New Model Update', 'description': 'Breaking news.', 'link': 'https://techcrunch.com/2'}
    now = datetime.now(timezone.utc)

    score_match = calculate_relevance_score(upper_case, now, [])
    score_no_match = calculate_relevance_score(no_match, now, [])

    assert score_match == pytest.approx(score_no_match + MOMENTUM_PRODUCT_BONUS)


def test_landmark_requires_flagship_plus_publisher_consensus():
    """is_landmark fires only for a named flagship covered by >= N independent publishers."""
    from src.config import LANDMARK_CONSENSUS_MIN_PUBLISHERS as MIN_PUBS
    now = datetime.now(timezone.utc)

    # flagship + enough independent publishers -> landmark
    covered = {'title': 'GPT-5 impressions roundup', 'description': 'analysis',
               'link': 'https://techcrunch.com/1', 'cross_publisher_domains': MIN_PUBS}
    calculate_relevance_score(covered, now, [])
    assert covered['is_landmark'] is True

    # same flagship, one publisher short -> NOT a landmark
    thin = {'title': 'GPT-5 impressions roundup', 'description': 'analysis',
            'link': 'https://techcrunch.com/2', 'cross_publisher_domains': MIN_PUBS - 1}
    calculate_relevance_score(thin, now, [])
    assert thin['is_landmark'] is False

    # widely covered but NO named flagship -> NOT a landmark
    no_flagship = {'title': 'A startup ships a new toaster', 'description': 'x',
                   'link': 'https://techcrunch.com/3', 'cross_publisher_domains': MIN_PUBS + 2}
    calculate_relevance_score(no_flagship, now, [])
    assert no_flagship['is_landmark'] is False


def test_landmark_is_not_inferred_from_launch_wording():
    """Launch *language* alone no longer creates a landmark (v4.25 simplification).

    The detector is measured (publisher consensus), not parsed, so a single-source
    "X launches GPT-5" is not a landmark — and neither are the shapes that used to
    fool the regex ("Unreleased GPT-5...", "Acme launches a tool ... from GPT-5").
    """
    now = datetime.now(timezone.utc)
    for title in ('OpenAI launches GPT-5',
                  'Unreleased GPT-5 benchmark results leak',
                  'Acme launches a migration tool for teams moving from GPT-5'):
        item = {'title': title, 'description': '', 'link': 'https://techcrunch.com/1'}
        calculate_relevance_score(item, now, [])
        assert item['is_landmark'] is False, title


def test_landmark_waives_topic_diversity_penalty():
    """A landmark is NOT hit by the -12 repetition penalty; a non-landmark still is."""
    from src.config import LANDMARK_CONSENSUS_MIN_PUBLISHERS as MIN_PUBS
    now = datetime.now(timezone.utc)

    landmark = {'title': 'GPT-5 reasoning results', 'description': 'analysis',
                'link': 'https://techcrunch.com/1', 'cross_publisher_domains': MIN_PUBS}
    score_fresh = calculate_relevance_score(dict(landmark), now, [])
    score_repeat = calculate_relevance_score(dict(landmark), now, ['LLMs'])
    assert score_repeat == pytest.approx(score_fresh)  # penalty waived

    # Control: a non-landmark LLM item still eats the -12.
    plain = {'title': 'Thoughts on LLM scaling', 'description': 'an essay',
             'link': 'https://techcrunch.com/2'}
    plain_fresh = calculate_relevance_score(dict(plain), now, [])
    plain_repeat = calculate_relevance_score(dict(plain), now, ['LLMs'])
    assert plain_repeat == pytest.approx(plain_fresh - 12.0)


def test_landmark_adds_launch_bonus():
    """A landmark scores exactly LANDMARK_LAUNCH_BONUS above the same item one
    publisher short of the threshold (which also drops the consensus step)."""
    from src.config import LANDMARK_LAUNCH_BONUS, CONSENSUS_SYNERGY_BONUS
    from src.config import LANDMARK_CONSENSUS_MIN_PUBLISHERS as MIN_PUBS
    now = datetime.now(timezone.utc)
    landmark = {'title': 'GPT-5 reasoning results', 'description': 'details',
                'link': 'https://techcrunch.com/1', 'cross_publisher_domains': MIN_PUBS}
    below = {'title': 'GPT-5 reasoning results', 'description': 'details',
             'link': 'https://techcrunch.com/1', 'cross_publisher_domains': MIN_PUBS - 1}

    score_landmark = calculate_relevance_score(landmark, now, [])
    score_below = calculate_relevance_score(below, now, [])
    assert below['is_landmark'] is False
    # The extra publisher adds one consensus step as well as the landmark bonus.
    assert score_landmark == pytest.approx(
        score_below + CONSENSUS_SYNERGY_BONUS + LANDMARK_LAUNCH_BONUS)


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
