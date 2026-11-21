import asyncio
import time
from types import SimpleNamespace

import feedparser

from src import net_safety, news


def _safe_response(text="<rss />", status_code=200):
    # mimic the httpx.Response surface get_with_safe_redirects returns
    return SimpleNamespace(text=text, is_redirect=False, status_code=status_code)


def test_fetch_single_feed_keeps_entries_when_bozo(monkeypatch):
    recent = time.gmtime()
    feed = feedparser.FeedParserDict(
        {
            "bozo": 1,
            "bozo_exception": ValueError("minor parse issue"),
            "entries": [
                feedparser.FeedParserDict(
                    {
                        "title": "Recoverable parse warning item",
                        "summary": "<p>Still valid.</p>",
                        "link": "https://example.com/news/1",
                        "published_parsed": recent,
                    }
                )
            ],
        }
    )

    async def _fake_safe_get(client, url, **kwargs):
        return _safe_response()

    monkeypatch.setattr(news, "get_with_safe_redirects", _fake_safe_get)
    monkeypatch.setattr(news.feedparser, "parse", lambda _: feed)

    result = asyncio.run(news.fetch_single_feed(object(), "https://example.com/rss"))

    assert result.ok is True
    assert result.entries_total == 1
    assert result.entries_accepted == 1
    assert result.error_type == "ValueError"  # bozo reason surfaced
    assert len(result.entries) == 1
    assert result.entries[0]["title"] == "Recoverable parse warning item"
    assert result.entries[0]["description"] == "Still valid."


def test_fetch_single_feed_caps_entries_and_keeps_newest(monkeypatch):
    now = time.time()
    feed = feedparser.FeedParserDict(
        {
            "bozo": 0,
            "entries": [
                feedparser.FeedParserDict(
                    {
                        "title": title,
                        "summary": "",
                        "link": f"https://example.com/news/{title}",
                        "published_parsed": time.gmtime(now - age_seconds),
                    }
                )
                for title, age_seconds in (
                    ("oldest", 300),
                    ("newest", 100),
                    ("middle", 200),
                )
            ],
        }
    )

    async def _fake_safe_get(client, url, **kwargs):
        return _safe_response()

    monkeypatch.setattr(news, "FEED_MAX_ACCEPTED_ENTRIES_PER_FEED", 2)
    monkeypatch.setattr(news, "get_with_safe_redirects", _fake_safe_get)
    monkeypatch.setattr(news.feedparser, "parse", lambda _: feed)

    result = asyncio.run(news.fetch_single_feed(object(), "https://example.com/rss"))

    assert result.ok is True
    assert result.entries_total == 3
    assert result.entries_accepted == 2
    assert [entry["title"] for entry in result.entries] == ["newest", "middle"]


def test_fetch_single_feed_uses_safe_fetch_without_metadata_policy(monkeypatch):
    """Feeds must go through the SSRF-guarded path, with the metadata domain
    allowlist disabled (feeds are their own trusted source list)."""
    captured = {}
    timeout = news.httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0)

    async def _fake_safe_get(client, url, **kwargs):
        captured.update(kwargs)
        return _safe_response()

    monkeypatch.setattr(news, "get_with_safe_redirects", _fake_safe_get)
    monkeypatch.setattr(news.feedparser, "parse",
                        lambda _: feedparser.FeedParserDict({"bozo": 0, "entries": []}))

    asyncio.run(news.fetch_single_feed(object(), "https://example.com/rss", timeout=timeout))

    assert captured.get("timeout") is timeout
    assert captured.get("enforce_metadata_policy") is False


def test_fetch_single_feed_blocked_redirect_returns_not_ok(monkeypatch):
    """When the SSRF guard blocks the feed (e.g. a redirect to a private IP),
    fetch_single_feed reports a failed fetch rather than raising."""
    async def _blocked(client, url, **kwargs):
        return None

    monkeypatch.setattr(news, "get_with_safe_redirects", _blocked)

    result = asyncio.run(news.fetch_single_feed(object(), "https://evil.example/rss"))

    assert result.ok is False
    assert result.error_type == "FetchFailedOrBlocked"
    assert result.entries == []


def test_resolver_pin_serialized_across_concurrent_fetches(monkeypatch):
    """The process-global resolver pin must never overlap across concurrent safe
    fetches, or a parallel feed would run with another feed's (or no) pin. The
    lock in get_with_safe_redirects must serialise the pinned section."""
    monkeypatch.setattr(net_safety, "_resolve_public_ip_candidates", lambda h: ["1.2.3.4"])
    state = {"cur": 0, "max": 0}

    async def _probe_capped_get(client, url, **kwargs):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.02)  # hold the pinned section open
        state["cur"] -= 1
        return _safe_response(text="ok")

    monkeypatch.setattr(net_safety, "_capped_stream_get", _probe_capped_get)

    async def _run():
        client = object()
        await asyncio.gather(
            news.get_with_safe_redirects(client, "https://a.example/x",
                                          enforce_metadata_policy=False),
            news.get_with_safe_redirects(client, "https://b.example/y",
                                          enforce_metadata_policy=False),
        )

    asyncio.run(_run())
    assert state["max"] == 1, "pinned fetches overlapped — resolver pin can be clobbered"


def test_fetch_single_feed_treats_non_2xx_as_failed(monkeypatch):
    """A 404/500 is a failed fetch, not a success that happens to return HTML.

    Without this the error page merely parsed to zero entries, so ok stayed True,
    last_ok_at refreshed every run, and the "broken" feed-health gate could never
    fire — a dead feed surfaced only as "stale" days later, or not at all if the
    error page carried parseable entries (Codex #106).
    """
    for status in (404, 500):
        async def _fake_safe_get(client, url, _status=status, **kwargs):
            return _safe_response(text="<html>Not Found</html>", status_code=_status)

        monkeypatch.setattr(news, "get_with_safe_redirects", _fake_safe_get)
        result = asyncio.run(news.fetch_single_feed(object(), "https://example.com/rss"))

        assert result.ok is False, status
        assert result.error_type == f"HTTP{status}"
        assert result.entries_total == 0
        assert result.entries_accepted == 0


def test_fetch_single_feed_still_ok_on_2xx(monkeypatch):
    """The status check must not reject ordinary success responses."""
    async def _fake_safe_get(client, url, **kwargs):
        return _safe_response(status_code=200)

    monkeypatch.setattr(news, "get_with_safe_redirects", _fake_safe_get)
    monkeypatch.setattr(news.feedparser, "parse",
                        lambda _: feedparser.FeedParserDict({"bozo": 0, "entries": []}))
    result = asyncio.run(news.fetch_single_feed(object(), "https://example.com/rss"))
    assert result.ok is True


def test_long_summary_is_kept_up_to_the_cap_and_marked_cut(monkeypatch):
    """The writer gets up to FEED_SUMMARY_MAX_CHARS, and a cut is visible (#306)."""
    feed = feedparser.FeedParserDict({"bozo": 0, "entries": [feedparser.FeedParserDict({
        "title": "Long post",
        "summary": "<p>" + "word " * 1000 + "</p>",
        "link": "https://example.com/news/long",
        "published_parsed": time.gmtime(),
    })]})

    async def _fake_safe_get(client, url, **kwargs):
        return _safe_response()

    monkeypatch.setattr(news, "get_with_safe_redirects", _fake_safe_get)
    monkeypatch.setattr(news.feedparser, "parse", lambda _: feed)
    description = asyncio.run(news.fetch_single_feed(object(), "https://example.com/rss")).entries[0]["description"]

    assert len(description) == news.FEED_SUMMARY_MAX_CHARS
    assert description.endswith("…")


def test_scoring_reads_only_the_first_part_of_the_summary():
    """A keyword past FEED_SUMMARY_SCORING_CHARS earns no bonus, so feeds that send
    whole posts do not outscore feeds that send teasers (#264, #306)."""
    from datetime import datetime, timezone

    def item(description):
        return {"title": "Plain title", "description": description, "link": "https://example.com/a"}

    def bonus(description):
        scored = item(description)
        news.calculate_relevance_score(scored, datetime.now(timezone.utc), [])
        return scored["score_parts"]["groundbreaking"]

    filler = "x" * news.FEED_SUMMARY_SCORING_CHARS
    assert bonus("a breakthrough " + filler) > 0
    assert bonus(filler + " a breakthrough") == 0
