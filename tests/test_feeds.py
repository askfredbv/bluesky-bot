"""Guards for the RSS feed list + source tiers (v4.25, 2026-09-03).

Locks in the primary-source coverage change: the dead Anthropic feed is gone,
the verified vendor blogs are present, and every new primary domain has a
SOURCE_TIERS entry (else it would score as an unknown tier-3 source).
"""
from src.config import RSS_FEEDS, SOURCE_TIERS


def test_dead_anthropic_feed_removed():
    # 404 since the claude.com rebrand; carrying it just fails every run.
    assert not any("anthropic.com/news.rss" in url for url in RSS_FEEDS)


def test_primary_vendor_feeds_present():
    for expected in (
        "https://blog.google/technology/ai/rss/",
        "https://blog.google/products/gemini/rss/",
        "https://mistral.ai/rss.xml",
        "https://developers.openai.com/rss.xml",
        "https://blogs.nvidia.com/feed/",
    ):
        assert expected in RSS_FEEDS, expected


def test_new_primary_domains_have_source_tiers():
    # developers.openai.com intentionally omitted: it matches "openai.com" via
    # the substring lookup in calculate_relevance_score.
    for domain in ("blog.google", "mistral.ai", "blogs.nvidia.com"):
        assert domain in SOURCE_TIERS, domain


def test_no_duplicate_feeds():
    assert len(RSS_FEEDS) == len(set(RSS_FEEDS))


def test_feeds_found_dead_on_2026_09_10_are_gone_or_replaced():
    # The first four alerted as "broken" (feed_persistently_unhealthy) on the
    # 2026-09-10 Curator run; Stanford's blog has not published since 2022.
    for dead in (
        "https://engineering.fb.com/category/ml-ai/feed/",  # 404
        "https://www.deeplearning.ai/the-batch/rss/",       # 403/404, no feed exists
        "https://stability.ai/blog?format=rss",             # 404
        "https://bair.berkeley.edu/blog/feed.xml",          # host unreachable 16+ days
        "https://ai.stanford.edu/blog/feed.xml",            # newest post June 2022
    ):
        assert dead not in RSS_FEEDS, dead
    for replacement in (
        "https://engineering.fb.com/category/ai-research/feed/",
        "https://stability.ai/news-updates?format=rss",
    ):
        assert replacement in RSS_FEEDS, replacement


def test_feeds_dropped_or_replaced_on_frederiks_call_2026_09_10():
    # Not broken: dormant (The Gradient) or too quiet for a 48-hour window
    # (vkrakovna). hnrss.org blocked the Actions runner on 20 of 28 fetches.
    for dropped in (
        "https://thegradient.pub/rss/",
        "https://vkrakovna.wordpress.com/feed/",
        "https://hnrss.org/best",
    ):
        assert dropped not in RSS_FEEDS, dropped
    assert "https://news.ycombinator.com/rss" in RSS_FEEDS
