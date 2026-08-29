import asyncio
import time
from types import SimpleNamespace

import feedparser

from src import utils


def _safe_response(text="<rss />"):
    # mimic the httpx.Response surface get_with_safe_redirects returns
    return SimpleNamespace(text=text, is_redirect=False)


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

    monkeypatch.setattr(utils, "get_with_safe_redirects", _fake_safe_get)
    monkeypatch.setattr(utils.feedparser, "parse", lambda _: feed)

    result = asyncio.run(utils.fetch_single_feed(object(), "https://example.com/rss"))

    assert result.ok is True
    assert result.entries_total == 1
    assert result.entries_accepted == 1
    assert result.error_type == "ValueError"  # bozo reason surfaced
    assert len(result.entries) == 1
    assert result.entries[0]["title"] == "Recoverable parse warning item"
    assert result.entries[0]["description"] == "Still valid."


def test_fetch_single_feed_uses_safe_fetch_without_metadata_policy(monkeypatch):
    """Feeds must go through the SSRF-guarded path, with the metadata domain
    allowlist disabled (feeds are their own trusted source list)."""
    captured = {}
    timeout = utils.httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0)

    async def _fake_safe_get(client, url, **kwargs):
        captured.update(kwargs)
        return _safe_response()

    monkeypatch.setattr(utils, "get_with_safe_redirects", _fake_safe_get)
    monkeypatch.setattr(utils.feedparser, "parse",
                        lambda _: feedparser.FeedParserDict({"bozo": 0, "entries": []}))

    asyncio.run(utils.fetch_single_feed(object(), "https://example.com/rss", timeout=timeout))

    assert captured.get("timeout") is timeout
    assert captured.get("enforce_metadata_policy") is False


def test_fetch_single_feed_blocked_redirect_returns_not_ok(monkeypatch):
    """When the SSRF guard blocks the feed (e.g. a redirect to a private IP),
    fetch_single_feed reports a failed fetch rather than raising."""
    async def _blocked(client, url, **kwargs):
        return None

    monkeypatch.setattr(utils, "get_with_safe_redirects", _blocked)

    result = asyncio.run(utils.fetch_single_feed(object(), "https://evil.example/rss"))

    assert result.ok is False
    assert result.error_type == "BlockedOrUnsafe"
    assert result.entries == []
