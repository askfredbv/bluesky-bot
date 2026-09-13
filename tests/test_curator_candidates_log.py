"""Curator runs log the news items offered to the model (2026-09-13).

Until now a post could only be traced to the one item it chose. On 2026-09-13 a
post named Sam Altman while its link never did, and the run log could not show
which offered item carried him.
"""
import pytest

from src import agents
from src.config import Mode

_ITEMS = [
    {"title": "We must pace the frontier", "description": "d", "link": "https://example.com/a",
     "score": 12.34, "detected_topic": "Policy/Society"},
    {"title": "Altman tells staff OpenAI is open to slowing AI development", "description": "d",
     "link": "https://example.com/b", "score": 9, "detected_topic": "LLMs"},
]


def _capture(monkeypatch):
    events = []

    def stop(*_args, **_kwargs):
        raise RuntimeError("stop here")

    monkeypatch.setattr(agents.SafeLogger, "info",
                        lambda event, message="", **fields: events.append((event, fields)))
    monkeypatch.setattr(agents, "_sync_generate", stop)
    return events


def _candidates(events):
    return [fields for event, fields in events if event == "curator_candidates"]


@pytest.mark.asyncio
async def test_a_curator_run_logs_every_item_it_offers_the_model(monkeypatch):
    events = _capture(monkeypatch)
    await agents.generate_content("key", [], mode=Mode.CURATOR, news_items=_ITEMS)
    assert _candidates(events) == [{"count": 2, "candidates": [
        {"title": "We must pace the frontier", "link": "https://example.com/a",
         "score": 12.3, "topic": "Policy/Society"},
        {"title": "Altman tells staff OpenAI is open to slowing AI development",
         "link": "https://example.com/b", "score": 9, "topic": "LLMs"},
    ]}]


@pytest.mark.asyncio
async def test_a_long_title_is_cut_and_a_missing_score_is_logged_as_none(monkeypatch):
    events = _capture(monkeypatch)
    item = {"title": "x" * 300, "description": "d", "link": "https://example.com/c"}
    await agents.generate_content("key", [], mode=Mode.CURATOR, news_items=[item])
    (logged,) = _candidates(events)
    assert logged["candidates"] == [{"title": "x" * 120, "link": "https://example.com/c",
                                     "score": None, "topic": None}]


@pytest.mark.asyncio
async def test_a_mentor_run_logs_no_candidates(monkeypatch):
    events = _capture(monkeypatch)
    await agents.generate_content("key", [], mode=Mode.MENTOR)
    assert _candidates(events) == []
