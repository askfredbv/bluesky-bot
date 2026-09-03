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
    # Identical items from same source and age — only difference is product name
    # in title. Deliberately NO launch word ("released"/"launches"): a momentum
    # name + a launch word is a landmark (v4.25) and would add LANDMARK_LAUNCH_BONUS
    # on top, which this test isolates away from. See the landmark tests below.
    base = {'title': 'New AI Model', 'description': 'Details', 'link': 'https://techcrunch.com/1'}
    flagship = {'title': 'claude 4 by Anthropic', 'description': 'Details', 'link': 'https://techcrunch.com/2'}
    now = datetime.now(timezone.utc)

    score_base = calculate_relevance_score(base, now, [])
    score_flagship = calculate_relevance_score(flagship, now, [])

    assert score_flagship == pytest.approx(score_base + MOMENTUM_PRODUCT_BONUS)


def test_momentum_product_bonus_case_insensitive():
    """Momentum matching is lowercase — title casing should not matter."""
    from src.config import MOMENTUM_PRODUCT_BONUS
    # No launch word, so momentum is isolated from the landmark bonus (see above).
    upper_case = {'title': 'GPT-5 Update', 'description': 'Breaking news.', 'link': 'https://techcrunch.com/1'}
    no_match = {'title': 'New Model Update', 'description': 'Breaking news.', 'link': 'https://techcrunch.com/2'}
    now = datetime.now(timezone.utc)

    score_match = calculate_relevance_score(upper_case, now, [])
    score_no_match = calculate_relevance_score(no_match, now, [])

    assert score_match == pytest.approx(score_no_match + MOMENTUM_PRODUCT_BONUS)


def test_landmark_detection_requires_momentum_plus_signal():
    """is_landmark is set only for a momentum flagship that is a launch or widely covered."""
    now = datetime.now(timezone.utc)

    # momentum + launch word → landmark
    momentum_launch = {'title': 'OpenAI launches GPT-5', 'description': 'x', 'link': 'https://techcrunch.com/1'}
    calculate_relevance_score(momentum_launch, now, [])
    assert momentum_launch['is_landmark'] is True

    # momentum only, no launch word, single publisher → NOT landmark
    momentum_only = {'title': 'GPT-5 impressions', 'description': 'a review', 'link': 'https://techcrunch.com/2'}
    calculate_relevance_score(momentum_only, now, [])
    assert momentum_only['is_landmark'] is False

    # launch word only, no momentum product → NOT landmark
    launch_only = {'title': 'Startup launches new app', 'description': 'x', 'link': 'https://techcrunch.com/3'}
    calculate_relevance_score(launch_only, now, [])
    assert launch_only['is_landmark'] is False


def test_landmark_launch_must_be_near_the_flagship():
    """A launch word far from the flagship (an unrelated launch in the same blurb)
    must NOT manufacture a landmark — the launch has to be about the flagship."""
    now = datetime.now(timezone.utc)
    # "gpt-5" and "launches" are >60 chars apart and about different things.
    far = {
        'title': 'GPT-5 remains the model to beat in most evaluations this year',
        'description': 'In entirely separate news, a small hardware startup launches a kitchen gadget.',
        'link': 'https://techcrunch.com/1',
    }
    calculate_relevance_score(far, now, [])
    assert far['is_landmark'] is False

    # Same words, now adjacent → about the flagship → landmark.
    near = {'title': 'OpenAI launches GPT-5', 'description': 'x', 'link': 'https://techcrunch.com/2'}
    calculate_relevance_score(near, now, [])
    assert near['is_landmark'] is True


def test_landmark_requires_launch_to_govern_the_flagship():
    """The launch verb must govern the flagship, not merely sit near it (Codex #106).

    'Acme launches a migration tool ... from GPT-5' has launch + flagship close
    together, but GPT-5 is the migration *source*, not the launched product."""
    now = datetime.now(timezone.utc)
    unrelated = {
        'title': 'Acme launches a migration tool for teams moving projects from GPT-5',
        'description': 'x', 'link': 'https://techcrunch.com/1',
    }
    calculate_relevance_score(unrelated, now, [])
    assert unrelated['is_landmark'] is False


def test_landmark_construction_both_orders_and_versioned():
    """Launch-verb→flagship and flagship→launch-verb both qualify, incl. version suffixes."""
    now = datetime.now(timezone.utc)
    cases = [
        'OpenAI launches GPT-5',              # verb → flagship
        'GPT-5 is now available',             # flagship → verb (multi-word signal)
        'Google unveils Gemini 3.8 today',    # version suffix on the flagship
        'Introducing GPT-5 to everyone',      # 'introduc' stem → flagship
        'Introducing: GPT-5',                 # punctuation between verb and flagship
        'GPT-5, released today by OpenAI',     # comma glued to the flagship
    ]
    for title in cases:
        item = {'title': title, 'description': '', 'link': 'https://techcrunch.com/x'}
        calculate_relevance_score(item, now, [])
        assert item['is_landmark'] is True, title


def test_landmark_does_not_cross_a_sentence_boundary():
    """A launch in a separate sentence must not attach to a flagship in another (Codex #106)."""
    now = datetime.now(timezone.utc)
    item = {
        'title': 'GPT-5 tops the benchmarks this quarter',
        'description': 'In unrelated news, a hardware startup released a new toaster.',
        'link': 'https://techcrunch.com/1',
    }
    calculate_relevance_score(item, now, [])
    assert item['is_landmark'] is False


def test_landmark_via_high_cross_publisher_consensus():
    """A momentum flagship covered by >= N independent publishers is a landmark even without a launch word."""
    from src.config import LANDMARK_CONSENSUS_MIN_PUBLISHERS
    now = datetime.now(timezone.utc)
    item = {
        'title': 'GPT-5 impressions roundup', 'description': 'analysis',
        'link': 'https://techcrunch.com/1',
        'cross_publisher_domains': LANDMARK_CONSENSUS_MIN_PUBLISHERS,
    }
    calculate_relevance_score(item, now, [])
    assert item['is_landmark'] is True


def test_landmark_waives_topic_diversity_penalty():
    """A landmark launch is NOT hit by the -12 repetition penalty; a non-landmark still is."""
    now = datetime.now(timezone.utc)

    landmark = {'title': 'OpenAI launches GPT-5', 'description': 'reasoning model', 'link': 'https://techcrunch.com/1'}
    score_fresh = calculate_relevance_score(dict(landmark), now, [])
    score_repeat = calculate_relevance_score(dict(landmark), now, ['LLMs'])
    assert score_repeat == pytest.approx(score_fresh)  # penalty waived

    # Control: a non-landmark LLM item still eats the -12.
    plain = {'title': 'Thoughts on LLM scaling', 'description': 'an essay', 'link': 'https://techcrunch.com/2'}
    plain_fresh = calculate_relevance_score(dict(plain), now, [])
    plain_repeat = calculate_relevance_score(dict(plain), now, ['LLMs'])
    assert plain_repeat == pytest.approx(plain_fresh - 12.0)


def test_landmark_adds_launch_bonus():
    """The landmark bonus is exactly LANDMARK_LAUNCH_BONUS above the same item without the momentum name.

    Removing the momentum name drops both the momentum bonus and landmark status,
    so the delta is MOMENTUM_PRODUCT_BONUS + LANDMARK_LAUNCH_BONUS; the launch
    word, source tier, and age are identical on both sides.
    """
    from src.config import MOMENTUM_PRODUCT_BONUS, LANDMARK_LAUNCH_BONUS
    now = datetime.now(timezone.utc)
    landmark = {'title': 'GPT-5 launches today', 'description': 'details', 'link': 'https://techcrunch.com/1'}
    non_landmark = {'title': 'model launches today', 'description': 'details', 'link': 'https://techcrunch.com/2'}

    score_landmark = calculate_relevance_score(landmark, now, [])
    score_plain = calculate_relevance_score(non_landmark, now, [])
    assert non_landmark['is_landmark'] is False
    assert score_landmark == pytest.approx(score_plain + MOMENTUM_PRODUCT_BONUS + LANDMARK_LAUNCH_BONUS)


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
