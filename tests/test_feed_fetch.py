import asyncio
import time
from types import SimpleNamespace

import feedparser

from src import utils


class _DummyClient:
    async def get(self, url, **kwargs):
        return SimpleNamespace(text="<rss />")


class _TimeoutCaptureClient:
    def __init__(self):
        self.last_timeout = None

    async def get(self, url, **kwargs):
        self.last_timeout = kwargs.get("timeout")
        return SimpleNamespace(text="<rss />")


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

    monkeypatch.setattr(utils.feedparser, "parse", lambda _: feed)

    result = asyncio.run(utils.fetch_single_feed(_DummyClient(), "https://example.com/rss"))

    assert result.ok is True
    assert result.entries_total == 1
    assert result.entries_accepted == 1
    assert result.error_type == "ValueError"  # bozo reason surfaced
    assert len(result.entries) == 1
    assert result.entries[0]["title"] == "Recoverable parse warning item"
    assert result.entries[0]["description"] == "Still valid."


def test_fetch_single_feed_passes_explicit_timeout():
    client = _TimeoutCaptureClient()
    timeout = utils.httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0)

    asyncio.run(utils.fetch_single_feed(client, "https://example.com/rss", timeout=timeout))

    assert client.last_timeout is timeout
