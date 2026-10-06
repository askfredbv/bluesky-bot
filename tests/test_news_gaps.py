"""Behaviour of src/news.py that the 2026-09-11 mutation run found unpinned.

Line numbers refer to news.py at 49af4a4. Left alone as equivalent:
  - L86 (Or -> And): a token set shorter than min_shared can never share
    min_shared tokens, so the shared-count check below returns False anyway;
  - L101 (union > 0 -> union > 1): past the shared-count check the union holds
    at least three tokens;
  - L300 (the client's follow_redirects): every request passes
    follow_redirects=False itself (net_safety._capped_stream_get), which
    overrides the client default.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import feedparser
import httpx
import pytest

from src import news
from src.metrics import FeedFetchResult

_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(news, "datetime", _FrozenDatetime)


def _score(title, link="https://example.com/a", *, age_hours=0.0):
    item = {"title": title, "description": "", "link": link}
    return news.calculate_relevance_score(item, _NOW - timedelta(hours=age_hours), []), item


# ---------------------------------------------------------------------------
# Exact scores (L121-L143). The existing tests compare two scores, so a
# constant offset or a changed weight that moves both went unnoticed.
# "Quiet weekly roundup" matches no keyword list.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title, link, expected", [
    ("Quiet weekly roundup", "https://techcrunch.com/a", 6.0),     # tier 6, nothing else
    ("Quiet weekly roundup", "https://example.com/a", 3.0),        # no tier: the 3.0 default
    ("Quiet weekly roundup", "https://arxiv.org/abs/2609.12345", 3.0),  # arXiv items link to arxiv.org: no tier
    ("Quiet weekly roundup", "http://[::1", 3.0),                  # unparseable link: 3.0 too
    ("Quiet launch roundup", "https://example.com/a", 8.0),        # product keyword: +5
    ("Quiet benchmark roundup", "https://example.com/a", 10.0),    # groundbreaking keyword: +7
])
def test_each_scoring_factor_adds_its_exact_weight(frozen, title, link, expected):
    assert _score(title, link)[0] == pytest.approx(expected)


def test_a_story_decays_at_the_configured_rate(frozen):
    expected = 3.0 - 10 * news.TIME_DECAY_PER_HOUR
    assert _score("Quiet weekly roundup", age_hours=10)[0] == pytest.approx(expected)


def test_time_decay_is_capped(frozen):
    """Decay saturates, so age alone can never erase an arbitrary amount."""
    very_old = _score("Quiet weekly roundup", age_hours=10_000)[0]
    assert very_old == pytest.approx(3.0 - news.TIME_DECAY_MAX)


def test_a_future_dated_item_is_never_awarded_points(frozen):
    """A negative age must not become a BONUS.

    Age was used unclamped, so `score -= age * rate` added points for a feed
    carrying a publication date in the future (timezone bug, scheduled post
    leaking early). An item dated 72h ahead scored 42.0 against a legitimate
    ceiling near 15 and would have won every run until its timestamp caught up.
    fetch_single_feed's lookback does not catch it — that only drops old items.
    """
    fresh = _score("Quiet weekly roundup", age_hours=0)[0]
    for ahead in (1, 12, 72, 10_000):
        assert _score("Quiet weekly roundup", age_hours=-ahead)[0] == pytest.approx(fresh)


# ---------------------------------------------------------------------------
# Topic detection (L148, L150)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title, topic", [
    ("Quiet nvidia roundup", "Compute/HW"),
    ("Quiet gpt nvidia roundup", "LLMs"),   # two topics match: the first in TOPIC_MAP wins
    ("Quiet weekly roundup", "General"),
])
def test_the_topic_is_the_first_one_whose_keywords_match(frozen, title, topic):
    assert _score(title)[1]["detected_topic"] == topic


# ---------------------------------------------------------------------------
# Clustering thresholds (L70, L71, L82, L94, L101, L112, L114)
# ---------------------------------------------------------------------------

def test_publisher_domain_drops_www_and_survives_a_broken_link():
    assert news._publisher_domain("https://www.theverge.com/x") == "theverge.com"
    assert news._publisher_domain("http://[::1") == ""


def test_three_shared_tokens_are_enough_to_cluster():
    tokens = frozenset({"openai", "launches", "sora"})
    assert news._titles_cluster(tokens, tokens) is True


def test_three_shared_tokens_in_a_mostly_different_headline_do_not_cluster():
    a = frozenset({"openai", "launches", "sora"})
    b = frozenset({"openai", "launches", "sora", "video", "tool", "today", "beta"})
    assert news._titles_cluster(a, b) is False  # Jaccard 3/7, under 0.5


def test_a_year_is_not_a_version_number():
    """Only single-digit tokens veto a cluster. A year next to an unversioned
    headline must not look like a different model version."""
    a = frozenset({"openai", "launches", "gpt", "5", "model"})
    b = frozenset({"openai", "launches", "gpt", "model", "2024"})
    assert news._titles_cluster(a, b) is True


def _item(title, link):
    return {"title": title, "description": "", "link": link, "source_feeds": [link]}


def test_a_three_token_headline_can_still_earn_consensus():
    items = [_item("OpenAI launches Sora", "https://openai.com/a"),
             _item("OpenAI launches Sora", "https://theverge.com/b")]
    news.annotate_cross_publisher_consensus(items)
    assert [i["cross_publisher_domains"] for i in items] == [2, 2]


def test_an_item_without_a_domain_is_not_counted_as_a_publisher():
    items = [_item("OpenAI launches GPT-5 reasoning model", "https://openai.com/a"),
             _item("OpenAI launches GPT-5 reasoning model", "/relative-link")]
    news.annotate_cross_publisher_consensus(items)
    assert [i["cross_publisher_domains"] for i in items] == [1, 1]


# ---------------------------------------------------------------------------
# fetch_single_feed results (L201-L278)
# ---------------------------------------------------------------------------

def _entry(title, *, days_old=0.1, link="https://example.com/n", date_field="published_parsed"):
    entry = {"title": title, "summary": "", "link": link}
    if days_old is not None:
        entry[date_field] = time.gmtime(time.time() - days_old * 86400)
    return feedparser.FeedParserDict(entry)


def _fetch(monkeypatch, *, entries=None, status_code=200, response="ok", raises=None):
    async def fake_get(client, url, **kwargs):
        if raises:
            raise raises
        if response is None:
            return None
        return SimpleNamespace(text="<rss />", is_redirect=False, status_code=status_code)

    monkeypatch.setattr(news, "get_with_safe_redirects", fake_get)
    monkeypatch.setattr(news.feedparser, "parse",
                        lambda _: feedparser.FeedParserDict({"bozo": 0, "entries": entries or []}))
    return asyncio.run(news.fetch_single_feed(object(), "https://example.com/rss"))


def _failed(result, error_type):
    return (result.ok, result.entries_total, result.entries_accepted, result.error_type) == (False, 0, 0, error_type)


def test_a_blocked_fetch_counts_no_entries(monkeypatch):
    assert _failed(_fetch(monkeypatch, response=None), "FetchFailedOrBlocked")


def test_a_300_response_is_a_failed_fetch(monkeypatch):
    assert _failed(_fetch(monkeypatch, status_code=300), "HTTP300")


@pytest.mark.parametrize("exc, error_type", [
    (httpx.ReadTimeout("slow"), "ReadTimeout"),
    (RuntimeError("boom"), "RuntimeError"),
])
def test_a_raised_error_becomes_a_failed_result(monkeypatch, exc, error_type):
    assert _failed(_fetch(monkeypatch, raises=exc), error_type)


def test_a_rejected_entry_skips_only_itself(monkeypatch):
    result = _fetch(monkeypatch, entries=[
        _entry("no date", days_old=None),
        _entry("too old", days_old=3),
        _entry("no link", link=""),
        _entry("kept"),
    ])
    assert (result.entries_total, result.entries_accepted) == (4, 1)
    assert [e["title"] for e in result.entries] == ["kept"]


def test_the_lookback_is_two_days(monkeypatch):
    result = _fetch(monkeypatch, entries=[_entry("recent", days_old=1.5), _entry("stale", days_old=2.5)])
    assert [e["title"] for e in result.entries] == ["recent"]


def test_an_entry_with_only_an_updated_date_is_kept(monkeypatch):
    result = _fetch(monkeypatch, entries=[_entry("updated only", date_field="updated_parsed")])
    assert [e["title"] for e in result.entries] == ["updated only"]


# ---------------------------------------------------------------------------
# fetch_news: telemetry, consensus, ranking and the hidden gem (L279-L357)
# ---------------------------------------------------------------------------

def _run_news(monkeypatch, items, *, feeds=("https://feed-a.com/rss",), **kwargs):
    """Run fetch_news on fixed items, scored by their "s" field."""
    calls = {"recorded": [], "saved": []}
    health = {"feeds": {}}

    async def fake_fetch(client, url, *, timeout=None):
        own = items if url == feeds[0] else []
        return FeedFetchResult(url=url, ok=True, entries_total=len(own), entries_accepted=len(own), entries=own)

    monkeypatch.setattr(news, "RSS_FEEDS", list(feeds))
    monkeypatch.setattr(news, "fetch_single_feed", fake_fetch)
    monkeypatch.setattr(news, "load_feed_health", lambda: health)
    monkeypatch.setattr(news, "record_feed_attempt", lambda h, r: calls["recorded"].append((h, r.url)))
    monkeypatch.setattr(news, "save_feed_health", lambda h: calls["saved"].append(h))
    monkeypatch.setattr(news, "check_feed_health_alerts", lambda *a, **k: [])
    monkeypatch.setattr(news, "calculate_relevance_score", lambda item, pub, topics: item["s"])
    result = asyncio.run(news.fetch_news(seen_links=[], recent_topics=[], **kwargs))
    return result, calls, health


def _story(n, score, host="example.com", title=None):
    return {"title": title or f"Story number {n} unrelated", "description": "",
            "link": f"https://{host}/{n}", "source_feeds": ["https://feed-a.com/rss"],
            "pub_date": _NOW, "s": score}


def test_every_feed_outcome_is_recorded_and_saved(monkeypatch):
    feeds = ("https://feed-a.com/rss", "https://feed-b.com/rss")
    _result, calls, health = _run_news(monkeypatch, [_story(1, 1.0)], feeds=feeds)
    assert calls["recorded"] == [(health, feeds[0]), (health, feeds[1])]
    assert calls["saved"] == [health]


def test_items_are_annotated_for_consensus_before_scoring(monkeypatch):
    title = "OpenAI launches GPT-5 reasoning model"
    result, _calls, _health = _run_news(monkeypatch, [_story(1, 2.0, "openai.com", title),
                                                      _story(2, 1.0, "theverge.com", title)])
    assert [i["cross_publisher_domains"] for i in result] == [2, 2]


def test_candidates_come_back_best_first_and_five_by_default(monkeypatch):
    scores = [3.0, 7.0, 1.0, 6.0, 5.0, 2.0, 4.0]
    result, _calls, _health = _run_news(monkeypatch, [_story(n, s) for n, s in enumerate(scores)])
    assert [i["s"] for i in result] == [7.0, 6.0, 5.0, 4.0, 3.0]


def test_the_best_hidden_gem_takes_the_last_slot_when_none_made_the_top(monkeypatch):
    items = [_story(1, 10.0), _story(2, 9.0), _story(3, 8.0), _story(4, 7.0),
             _story(5, 6.0, "arxiv.org"), _story(6, 5.0, "arxiv.org")]
    result, _calls, _health = _run_news(monkeypatch, items, limit=3)
    assert [i["link"] for i in result] == ["https://example.com/1", "https://example.com/2",
                                           "https://arxiv.org/5"]


def test_no_gem_is_forced_in_when_one_already_made_the_top(monkeypatch):
    items = [_story(1, 10.0), _story(2, 9.0, "arxiv.org"), _story(3, 8.0),
             _story(4, 7.0), _story(5, 6.0, "arxiv.org")]
    result, _calls, _health = _run_news(monkeypatch, items, limit=3)
    assert [i["link"] for i in result] == ["https://example.com/1", "https://arxiv.org/2",
                                           "https://example.com/3"]


# ---------------------------------------------------------------------------
# Candidate source mix (select_candidates). The shortlist is a MENU the Curator
# picks one item from, so its job is representativeness. In production it was
# 75% arXiv, with 13 of 21 runs offering nothing else at all.
# ---------------------------------------------------------------------------

def _cand(n, score, host="example.com"):
    return {"title": f"story {n}", "link": f"https://{host}/{n}", "score": score}


def test_research_sources_cannot_take_every_slot():
    """A nightly preprint batch is capped, even when it sweeps the ranking."""
    ranked = [_cand(n, 20.0 - n, "arxiv.org") for n in range(6)]
    ranked += [_cand(10 + n, 5.0 - n) for n in range(4)]
    result = news.select_candidates(ranked, 5)

    assert len(result) == 5
    # news._is_gem, not a substring check on the URL: asserting through the real
    # predicate is both more faithful and avoids re-teaching the loose pattern.
    assert sum(1 for i in result if news._is_gem(i["link"])) == news.MAX_GEM_CANDIDATES


def test_the_gem_cap_does_not_shrink_the_shortlist():
    """The cap limits representation, not list length.

    An arXiv-only morning is the normal case here, not an edge case. Holding the
    cap hard would hand the model two candidates instead of five — a thinner
    menu, which is the opposite of the point.
    """
    ranked = [_cand(n, 20.0 - n, "arxiv.org") for n in range(8)]
    result = news.select_candidates(ranked, 5)

    assert len(result) == 5
    assert [i["score"] for i in result] == sorted((i["score"] for i in result), reverse=True)


def test_a_primary_source_is_promoted_into_the_shortlist():
    """Hidden Gem Injection guaranteed arXiv a seat; nothing guaranteed one to a
    primary source, so the asymmetry was built in. Both ends are bounded now."""
    ranked = [_cand(n, 20.0 - n, "arxiv.org") for n in range(3)]
    ranked += [_cand(10 + n, 10.0 - n, "lwn.net") for n in range(4)]
    ranked += [_cand(20, 1.0, "openai.com"), _cand(21, 0.5, "blog.google")]
    result = news.select_candidates(ranked, 5)

    assert sum(1 for i in result if news._is_tier1(i["link"])) >= news.MIN_TIER1_CANDIDATES


def test_the_primary_source_floor_never_fabricates():
    """No primary source in the pool means none in the shortlist — not an error,
    and not a lower-tier item relabelled."""
    ranked = [_cand(n, 10.0 - n, "lwn.net") for n in range(8)]
    result = news.select_candidates(ranked, 5)

    assert len(result) == 5
    assert not any(news._is_tier1(i["link"]) for i in result)


def test_a_floor_never_promotes_a_negative_scoring_item():
    """A floor is a diversity guarantee, not a licence to ship a bad candidate."""
    ranked = [_cand(n, 10.0 - n, "lwn.net") for n in range(6)]
    ranked += [_cand(20, -4.0, "openai.com"), _cand(21, -9.0, "blog.google")]
    result = news.select_candidates(ranked, 5)

    assert not any(news._is_tier1(i["link"]) for i in result)
    assert all(i["score"] > 0 for i in result)


def test_the_two_floors_do_not_starve_each_other():
    """Promoting primary sources must not evict the last research item, nor the
    reverse — the gem floor and the tier-1 floor have to coexist in 5 slots."""
    ranked = [_cand(n, 10.0 - n, "lwn.net") for n in range(5)]
    ranked += [_cand(10, 4.0, "arxiv.org")]
    ranked += [_cand(20, 3.0, "openai.com"), _cand(21, 2.0, "deepmind.google")]
    result = news.select_candidates(ranked, 5)

    assert len(result) == 5
    assert sum(1 for i in result if news._is_gem(i["link"])) >= news.MIN_GEM_CANDIDATES
    assert sum(1 for i in result if news._is_tier1(i["link"])) >= news.MIN_TIER1_CANDIDATES


@pytest.mark.parametrize("link", [
    "https://arxiv.org.evil.example/paper",   # trusted domain as a prefix label
    "https://notarxiv.org/paper",             # trusted domain as a substring
    "https://evil.example/?u=arxiv.org",      # trusted domain in the query
    "https://evil.example/arxiv.org/paper",   # trusted domain in the path
    "https://arxiv.org@attacker.example/p",   # trusted domain as userinfo
])
def test_a_lookalike_host_is_not_a_research_source(link):
    """The reserved research slot is host-anchored too.

    `gem in link` was a substring test over the WHOLE URL, so a trusted domain
    anywhere in it -- path, query, userinfo -- claimed the reserved gem slot,
    and the Hacker News and Lobsters feeds carry arbitrary submitted URLs.
    (CodeQL py/incomplete-url-substring-sanitization, on PR #173.)
    """
    assert not news._is_gem(link)


@pytest.mark.parametrize("link", [
    "https://arxiv.org/abs/2609.40027",
    "https://export.arxiv.org/abs/2609.40027",
    "https://bair.berkeley.edu/blog/x",
])
def test_real_research_sources_are_still_recognised(link):
    """Anchoring must not cost the matches HIDDEN_GEM_SOURCES is for."""
    assert news._is_gem(link)


@pytest.mark.parametrize("link", [
    "https://notopenai.com/x",
    "https://openai.com.evil.example/x",
    "https://openai.com@attacker.example/x",
    "https://fakeopenai.com/x",
    "https://deepmind.google.phish.example/x",
])
def test_a_lookalike_host_does_not_get_a_primary_source_tier(link):
    """Tier matching is hostname-anchored, not a substring test.

    The substring form handed OpenAI's tier-10 to any host merely CONTAINING
    "openai.com". That was always wrong but only inflated a score; once
    _is_tier1 began guaranteeing shortlist seats it became a way to force a
    spoofed host in as an AI lab's own announcement -- and items do arrive from
    arbitrary third-party domains through the Hacker News and Lobsters feeds.
    """
    assert news.source_tier(link) == news.DEFAULT_SOURCE_TIER
    assert not news._is_tier1(link)


@pytest.mark.parametrize("link, expected", [
    ("https://openai.com/index/x", 10.0),            # exact
    ("https://developers.openai.com/rss.xml", 10.0),  # documented subdomain
    ("https://www.theregister.com/a/b", 7.0),         # www. prefix
    ("https://blog.google/innovation-and-ai/x", 10.0),
    ("https://lwn.net/Articles/1", 9.0),
])
def test_real_hosts_keep_their_configured_tier(link, expected):
    """The suffix rule must not break the intended matches it replaced."""
    assert news.source_tier(link) == expected


def test_the_gem_cap_never_prefers_a_negative_item_to_a_positive_gem():
    """A capped gem may only lose its slot to a candidate worth offering.

    Applying the cap across the whole ranked list let a negative non-gem take a
    slot from a positive gem -- gems at 10/9/8/7 with non-gems at -1/-2/-3
    produced [10, 9, -1, -2, -3], contradicting this function's own rule that a
    negative score is a bad candidate. (Found in review of #169.)
    """
    ranked = [_cand(n, s, "arxiv.org") for n, s in enumerate([10.0, 9.0, 8.0, 7.0])]
    ranked += [_cand(10 + n, s) for n, s in enumerate([-1.0, -2.0, -3.0])]
    result = news.select_candidates(ranked, 5)

    scores = [i["score"] for i in result]
    assert scores[:4] == [10.0, 9.0, 8.0, 7.0]
    assert all(s > 0 for s in scores[:4])
    # Still fills to `limit`, so downstream callers keep their assumption.
    assert len(result) == 5


def test_the_shortlist_comes_back_best_first_after_promotion():
    """Downstream reads news_items[0] as the top item, so order must hold."""
    ranked = [_cand(n, 20.0 - n, "arxiv.org") for n in range(4)]
    ranked += [_cand(20, 6.0, "openai.com"), _cand(21, 5.0, "deepmind.google")]
    ranked += [_cand(30 + n, 3.0 - n) for n in range(3)]
    result = news.select_candidates(ranked, 5)

    assert [i["score"] for i in result] == sorted((i["score"] for i in result), reverse=True)
