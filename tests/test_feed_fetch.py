import asyncio
import time
from types import SimpleNamespace

import feedparser

from src import utils


class _DummyClient:
    async def get(self, url):
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

    items = asyncio.run(utils.fetch_single_feed(_DummyClient(), "https://example.com/rss"))

    assert len(items) == 1
    assert items[0]["title"] == "Recoverable parse warning item"
    assert items[0]["description"] == "Still valid."
