"""Tests for scripts/refresh_momentum — the pure, safety-critical parts.

The Gemini call is best-effort; what MUST be correct is (1) the sanitiser that
stands between raw model output and a source file (the injection guard),
(2) the config rewriter (must produce valid Python and touch only the list
body), and (3) the feed fetch going through the repo's SSRF guard rather than
around it.
"""
import httpx
import pytest

import scripts.refresh_momentum as rm
from scripts.refresh_momentum import rewrite_momentum_products, sanitize_products


class _FakeResp:
    def __init__(self, text):
        self.text = text


def test_ask_gemini_continues_failover_until_enough_usable(monkeypatch):
    """A response that sanitises to too few names must NOT end failover, and the
    thinking budget must be pinned so the tight output cap isn't eaten."""
    from google import genai

    calls = []
    many = "[" + ", ".join(f'"name-{i}"' for i in range(10)) + "]"

    class _FakeModels:
        def generate_content(self, model, contents, config):
            calls.append((model, config))
            return _FakeResp('["gpt-5"]' if len(calls) == 1 else many)

    class _FakeClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(genai, "Client", _FakeClient)
    out = rm._ask_gemini("key", ["headline"] * 10)

    assert len(out) >= rm.MIN_PRODUCTS          # usable names returned
    assert len(calls) >= 2                       # did not stop on the thin first reply
    assert any("thinking_config" in cfg for _, cfg in calls)  # budget pinned


def test_ask_gemini_returns_empty_when_whole_chain_thin(monkeypatch):
    from google import genai

    class _FakeModels:
        def generate_content(self, model, contents, config):
            return _FakeResp('["only-one"]')  # always below MIN_PRODUCTS

    class _FakeClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(genai, "Client", _FakeClient)
    assert rm._ask_gemini("key", ["headline"] * 10) == []

_SNIPPET = '''from typing import List

# a comment above the list must survive
MOMENTUM_PRODUCTS: List[str] = [
    "gpt-5", "claude 4",
]
MOMENTUM_PRODUCT_BONUS: float = 4.0
'''


def test_sanitize_drops_unsafe_lowercases_dedupes():
    raw = [
        "GPT-6", "  Claude 5  ", "gpt-6",          # case / whitespace / dup
        'evil"; import os', "x" * 40,               # injection attempt + too long
        123, None, "", "grok 4", "deepseek-v4.1",   # non-str / empty / valid
    ]
    out = sanitize_products(raw)
    assert out == ["gpt-6", "claude 5", "grok 4", "deepseek-v4.1"]
    # the injection guard: nothing that could break the quoted literal survives
    assert not any('"' in x or ";" in x or "\n" in x for x in out)


def test_sanitize_caps_count():
    assert len(sanitize_products([f"model-{i}" for i in range(50)])) <= 18


def test_rewrite_replaces_body_and_stays_valid_python():
    new = ["gpt-6", "claude 5", "gemini 4"]
    out = rewrite_momentum_products(_SNIPPET, new)

    ns: dict = {}
    exec(out, ns)  # must be valid, importable Python
    assert ns["MOMENTUM_PRODUCTS"] == new
    # neighbouring lines are untouched
    assert ns["MOMENTUM_PRODUCT_BONUS"] == 4.0
    assert "a comment above the list must survive" in out


def test_rewrite_raises_when_anchor_missing():
    with pytest.raises(ValueError):
        rewrite_momentum_products("x = 1\n", ["gpt-6"])


# -- Feed fetch: the transport must be the guarded one ------------------------
# External scan, 2026-10-13: this script ran its own httpx client with
# follow_redirects=True and no public-address, DNS-pin, per-hop, scheme or
# body-size checks, while every other fetch in the repo went through them.

_RSS = (
    b"<?xml version='1.0'?><rss version='2.0'><channel>"
    b"<item><title>Gemini 3 ships</title></item>"
    b"<item><title>Claude 5 ships</title></item>"
    b"</channel></rss>"
)


class _FakeFeedResp:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


def _guard_returning(*responses):
    """Build a fake get_with_safe_redirects that records how it was called."""
    calls = []
    queue = list(responses)

    async def guard(_client, url, *, timeout=None, enforce_metadata_policy=True):
        calls.append({"url": url, "enforce_metadata_policy": enforce_metadata_policy,
                      "timeout": timeout})
        result = queue.pop(0) if queue else None
        if isinstance(result, Exception):
            raise result
        return result

    return guard, calls


async def test_fetch_headlines_goes_through_the_ssrf_guard(monkeypatch):
    guard, calls = _guard_returning(_FakeFeedResp(200, _RSS))
    monkeypatch.setattr(rm, "get_with_safe_redirects", guard)
    monkeypatch.setattr(rm, "RSS_FEEDS", ["https://example.com/feed.xml"])

    headlines = await rm._fetch_headlines()

    assert headlines == ["Gemini 3 ships", "Claude 5 ships"]
    assert [c["url"] for c in calls] == ["https://example.com/feed.xml"]
    # Feeds are their own trusted list, so the metadata domain allowlist is
    # off -- but public-IP validation, DNS pinning and per-hop redirect checks
    # all stay on. Same call shape as src/news.py's fetch_single_feed.
    assert calls[0]["enforce_metadata_policy"] is False
    assert calls[0]["timeout"] is rm._FEED_TIMEOUT


async def test_fetch_headlines_never_uses_a_raw_client_get(monkeypatch):
    """The regression lock: a direct client.get skips every control above."""
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("raw client.get bypasses the SSRF guard")

    guard, _calls = _guard_returning(_FakeFeedResp(200, _RSS))
    monkeypatch.setattr(httpx.AsyncClient, "get", forbidden)
    monkeypatch.setattr(rm, "get_with_safe_redirects", guard)
    monkeypatch.setattr(rm, "RSS_FEEDS", ["https://example.com/feed.xml"])

    assert await rm._fetch_headlines() == ["Gemini 3 ships", "Claude 5 ships"]


async def test_fetch_headlines_drops_a_blocked_feed(monkeypatch):
    """A None from the guard is a block or a transport failure; neither posts."""
    guard, _calls = _guard_returning(None)
    monkeypatch.setattr(rm, "get_with_safe_redirects", guard)
    monkeypatch.setattr(rm, "RSS_FEEDS", ["https://blocked.example/feed.xml"])

    assert await rm._fetch_headlines() == []


async def test_fetch_headlines_ignores_a_non_success_status(monkeypatch):
    """A 404 page that happens to parse must not become headlines."""
    guard, _calls = _guard_returning(_FakeFeedResp(404, _RSS))
    monkeypatch.setattr(rm, "get_with_safe_redirects", guard)
    monkeypatch.setattr(rm, "RSS_FEEDS", ["https://example.com/feed.xml"])

    assert await rm._fetch_headlines() == []


async def test_fetch_headlines_survives_one_feed_raising(monkeypatch):
    """Best-effort per feed: one bad feed must not lose the others."""
    guard, _calls = _guard_returning(RuntimeError("boom"), _FakeFeedResp(200, _RSS))
    monkeypatch.setattr(rm, "get_with_safe_redirects", guard)
    monkeypatch.setattr(rm, "RSS_FEEDS",
                        ["https://bad.example/feed.xml", "https://ok.example/feed.xml"])

    assert await rm._fetch_headlines() == ["Gemini 3 ships", "Claude 5 ships"]
