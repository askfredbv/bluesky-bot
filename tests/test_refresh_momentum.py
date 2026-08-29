"""Tests for scripts/refresh_momentum — the pure, safety-critical parts.

The network + Gemini calls are best-effort and untested here; what MUST be
correct is (1) the sanitiser that stands between raw model output and a source
file (the injection guard) and (2) the config rewriter (must produce valid
Python and touch only the list body).
"""
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
