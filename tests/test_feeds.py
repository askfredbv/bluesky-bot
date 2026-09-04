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
