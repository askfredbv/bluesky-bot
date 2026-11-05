"""The Curator grounding check: a post may claim only what its source says.

Built after the 2026-11-05 Krebs post, which turned "security experts warned"
and a founder's former hosting firm into flat facts.
"""
import json

import pytest

from src import agents
from src.agents import _grounding_reviewer, check_curator_grounding as _real_check

_KREBS = {
    "title": "Want to Rent an Internet Address at the White House?",
    "description": (
        "Several staffers at the White House are using computers or mobile devices "
        "equipped with residential proxy services that render their internet address "
        "available for others to rent, security experts warned today."
    ),
    "link": "https://krebsonsecurity.com/x",
}
_CHAIN = ["claude-sonnet-5-5", "claude-haiku-5-5", "gemini-3.7-flash", "gemini-3.5-flash"]
_POST = "I keep seeing this exact failure mode in production and the tooling still does not catch it."


def _reviewer_returns(monkeypatch, response, seen=None):
    def _gen(_key, _system, task, model, _anthropic=None, _timeout=None):
        if seen is not None:
            seen.append((task, model))
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(agents, "_sync_generate", _gen)


def test_the_reviewer_comes_from_the_other_provider():
    assert _grounding_reviewer("claude-sonnet-5-5", _CHAIN) == "gemini-3.7-flash"
    assert _grounding_reviewer("gemini-3.7-flash", _CHAIN) == "claude-haiku-5-5"


def test_one_provider_falls_back_to_a_distinct_model_and_one_model_to_none():
    assert _grounding_reviewer("gemini-3.7-flash", ["gemini-3.7-flash", "gemini-3.5-flash"]) == "gemini-3.5-flash"
    assert _grounding_reviewer("gemini-3.7-flash", ["gemini-3.7-flash"]) is None


@pytest.mark.asyncio
async def test_the_reviewer_sees_the_source_the_writer_saw(monkeypatch):
    seen = []
    _reviewer_returns(monkeypatch, '{"grounded": true, "problems": []}', seen)
    ok, problems = await _real_check("k", ["post text"], _KREBS, "claude-sonnet-5-5", _CHAIN)
    assert (ok, problems) == (True, [])
    task, model = seen[0]
    assert model == "gemini-3.7-flash"
    assert "security experts warned today" in task and "post text" in task
    assert "Published by: krebsonsecurity.com" in task  # naming the publisher is not an invented name


@pytest.mark.asyncio
async def test_a_failed_verdict_returns_its_problems(monkeypatch):
    _reviewer_returns(monkeypatch, json.dumps(
        {"grounded": False, "problems": ["'browses as the White House' escalates an IP into an identity"]}))
    ok, problems = await _real_check("k", ["p"], _KREBS, "claude-sonnet-5-5", _CHAIN)
    assert ok is False and "identity" in problems[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    '{"grounded": true, "problems": ["but this claim is not in the source"]}',  # contradicts itself
    '{"grounded": false, "problems": []}',  # a fail that names nothing
    '{"grounded": "yes", "problems": []}',
    '["grounded"]',
    "not json",
])
async def test_an_unusable_verdict_fails_closed(monkeypatch, response):
    _reviewer_returns(monkeypatch, response)
    ok, _ = await _real_check("k", ["p"], _KREBS, "claude-sonnet-5-5", _CHAIN)
    assert ok is False


@pytest.mark.asyncio
async def test_a_failed_call_or_no_reviewer_fails_closed(monkeypatch):
    _reviewer_returns(monkeypatch, RuntimeError("quota"))
    assert (await _real_check("k", ["p"], _KREBS, "claude-sonnet-5-5", _CHAIN))[0] is False
    assert (await _real_check("k", ["p"], _KREBS, "gemini-3.7-flash", ["gemini-3.7-flash"]))[0] is False


def _curator_writer(monkeypatch, verdicts):
    """The writer always returns the same post; each check pops the next verdict."""
    calls = []

    def _gen(*_a, **_k):
        return json.dumps({"url": _KREBS["link"], "posts": [_POST]})

    async def _check(_key, posts, item, writer, chain, _anthropic=None):
        calls.append((posts, item["link"], writer))
        return verdicts.pop(0)

    monkeypatch.setattr(agents, "_sync_generate", _gen)
    monkeypatch.setattr(agents, "check_curator_grounding", _check)
    return calls


@pytest.mark.asyncio
async def test_an_ungrounded_post_is_regenerated(monkeypatch):
    calls = _curator_writer(monkeypatch, [(False, ["overstated"]), (True, [])])
    content, _, link = await agents.generate_content(
        "k", [], mode="curator", news_items=[_KREBS], model_priority=["gemini-3.7-flash"])
    assert content == [_POST] and link == _KREBS["link"]
    assert len(calls) == 2 and calls[0][1] == _KREBS["link"]


@pytest.mark.asyncio
async def test_a_post_that_never_grounds_is_not_shipped(monkeypatch):
    _curator_writer(monkeypatch, [(False, ["overstated"])] * 10)
    content, _, _ = await agents.generate_content(
        "k", [], mode="curator", news_items=[_KREBS], model_priority=["gemini-3.7-flash"])
    assert content == []
