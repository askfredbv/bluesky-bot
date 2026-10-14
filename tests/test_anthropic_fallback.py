"""Cross-provider fallback: dispatch, isolation, and the no-key guarantee.

The fallback only fires when every Gemini model has failed, which means it
will essentially never run in production and cannot be verified by watching
daily runs. These tests plus scripts/probe_fallback.py are the only evidence
it still works, so they cover the failure paths deliberately.
"""

import json
import types

import pytest

from src import llm
from src.agents import _sync_generate, generate_content
from src.config import ANTHROPIC_CHAIN_POSITION, ANTHROPIC_MODELS, MAX_OUTPUT_TOKENS
from src.llm import (
    compose_model_chain,
    generate_anthropic,
    is_anthropic_model,
    thinking_off_for,
)
from src.settings import Settings


def _block(text):
    return types.SimpleNamespace(type="text", text=text)


def _fake_anthropic_module(recorder, blocks=None, raises=None):
    """Minimal stand-in for the `anthropic` package, injected via sys.modules."""

    class _Messages:
        def create(self, **kwargs):
            recorder.append(kwargs)
            if raises is not None:
                raise raises
            return types.SimpleNamespace(
                content=blocks if blocks is not None else [_block("ok")]
            )

    class _Anthropic:
        def __init__(self, api_key=None, timeout=None, max_retries=None):
            recorder.append({"_init_api_key": api_key, "_init_timeout": timeout,
                             "_init_max_retries": max_retries})
            self.messages = _Messages()

    return types.SimpleNamespace(Anthropic=_Anthropic)


# --------------------------------------------------------------- model routing


@pytest.mark.parametrize(
    "model, expected",
    [
        ("claude-haiku-5-5", True),
        ("CLAUDE-HAIKU-5-5", True),
        ("gemini-3.7-flash", False),
        ("gemini-2.5-pro", False),
        ("gemma-3-27b-it", False),
    ],
)
def test_is_anthropic_model(model, expected):
    assert is_anthropic_model(model) is expected


def test_sync_generate_routes_claude_models_away_from_gemini(monkeypatch):
    """A claude-* model must never touch the Gemini client."""
    calls = []
    monkeypatch.setattr(
        "src.agents._get_client",
        lambda _k: pytest.fail("Gemini client built for an Anthropic model"),
    )
    monkeypatch.setattr(
        "src.llm.generate_anthropic",
        lambda *a, **k: calls.append(a) or '{"url": "u", "posts": ["p"]}',
    )
    # src.agents imported the symbol directly, so patch it there too.
    monkeypatch.setattr(
        "src.agents.generate_anthropic",
        lambda *a, **k: calls.append(a) or '{"url": "u", "posts": ["p"]}',
    )
    out = _sync_generate("gemini-key", "sys", "task", "claude-haiku-5-5", "ant-key")
    assert out == '{"url": "u", "posts": ["p"]}'
    # key, system, task, model, max_tokens
    assert calls[0][0] == "ant-key"
    assert calls[0][3] == "claude-haiku-5-5"
    assert calls[0][4] == MAX_OUTPUT_TOKENS


def test_sync_generate_still_uses_gemini_for_gemini_models(monkeypatch):
    monkeypatch.setattr(
        "src.agents.generate_anthropic",
        lambda *a, **k: pytest.fail("Anthropic called for a Gemini model"),
    )
    captured = {}

    class _Models:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(text="from-gemini")

    monkeypatch.setattr(
        "src.agents._get_client",
        lambda _k: types.SimpleNamespace(models=_Models()),
    )
    assert _sync_generate("k", "sys", "task", "gemini-3.7-flash") == "from-gemini"
    assert captured["model"] == "gemini-3.7-flash"


# ------------------------------------------------------------ the Anthropic call


def test_generate_anthropic_pins_thinking_off_and_joins_text(monkeypatch):
    rec = []
    monkeypatch.setitem(
        __import__("sys").modules, "anthropic", _fake_anthropic_module(rec, [_block("a"), _block("b")])
    )
    out = generate_anthropic("key", "sys", "task", "claude-haiku-5-5", 1500)
    assert out == "ab"
    call = rec[-1]
    assert call["thinking"] == {"type": "disabled"}, "thinking must be off"
    assert call["max_tokens"] == 1500
    assert call["system"] == "sys"
    assert call["messages"] == [{"role": "user", "content": "task"}]


def test_generate_anthropic_returns_empty_when_no_text_block(monkeypatch):
    """A refusal yields no text block; callers treat "" as no-content."""
    rec = []
    monkeypatch.setitem(
        __import__("sys").modules,
        "anthropic",
        _fake_anthropic_module(rec, [types.SimpleNamespace(type="thinking", text="x")]),
    )
    assert generate_anthropic("key", "sys", "task", "claude-haiku-5-5", 1500) == ""


@pytest.mark.parametrize("key", [None, "", "   "])
def test_generate_anthropic_raises_without_a_key(key):
    """Must raise, not silently return empty: the chain logs and moves on."""
    with pytest.raises(ValueError, match="Anthropic API key"):
        generate_anthropic(key, "sys", "task", "claude-haiku-5-5", 1500)


def test_sync_generate_does_not_silently_fall_back_to_gemini_without_key(monkeypatch):
    """No key + a claude model is an error, never a quiet Gemini call."""
    monkeypatch.setattr(
        "src.agents._get_client",
        lambda _k: pytest.fail("fell through to Gemini"),
    )
    with pytest.raises(ValueError, match="Anthropic API key"):
        _sync_generate("gemini-key", "sys", "task", "claude-haiku-5-5", None)


# -------------------------------------------------------- end-to-end chain order


@pytest.mark.asyncio
async def test_chain_reaches_anthropic_only_after_gemini_models_fail(monkeypatch):
    seen = []

    def _fake(api_key, instr, task, model, anthropic_api_key=None):
        seen.append(model)
        if not is_anthropic_model(model):
            raise RuntimeError("gemini down")
        return json.dumps(
            {
                "url": "https://example.com/a",
                "posts": ["A sentence long enough to pass the validators, with substance."],
            }
        )

    monkeypatch.setattr("src.agents._sync_generate", _fake)
    posts, _topic, link = await generate_content(
        "gemini-key",
        [],
        news_items=[{"title": "T", "description": "d", "link": "https://example.com/a"}],
        mode=__import__("src.config", fromlist=["Mode"]).Mode.CURATOR,
        model_priority=["gemini-3.7-flash", "claude-haiku-5-5"],
        anthropic_api_key="ant-key",
    )
    assert posts, "fallback should have produced a post"
    assert link == "https://example.com/a"
    assert seen[0] == "gemini-3.7-flash", "Gemini must be tried first"
    assert "claude-haiku-5-5" in seen, "fallback never reached"


@pytest.mark.asyncio
async def test_anthropic_output_still_passes_through_the_validators(monkeypatch):
    """Garbage from the fallback must skip the post, not bypass validation."""

    def _fake(api_key, instr, task, model, anthropic_api_key=None):
        if not is_anthropic_model(model):
            raise RuntimeError("gemini down")
        return "Not JSON at all, just prose the model felt like writing."

    monkeypatch.setattr("src.agents._sync_generate", _fake)
    posts, _topic, _link = await generate_content(
        "gemini-key",
        [],
        news_items=[{"title": "T", "description": "d", "link": "https://example.com/a"}],
        mode=__import__("src.config", fromlist=["Mode"]).Mode.CURATOR,
        model_priority=["gemini-3.7-flash", "claude-haiku-5-5"],
        anthropic_api_key="ant-key",
    )
    assert posts == [], "invalid fallback output must not be published"


# ------------------------------------------------------------- the no-key path


def test_anthropic_key_is_optional_in_settings():
    env = {
        "GEMINI_API_KEY": "g",
        "BLUESKY_APP_PASSWORD": "p",
    }
    assert Settings.from_env(env).credentials.anthropic_api_key is None


def test_anthropic_key_is_read_when_present():
    env = {
        "GEMINI_API_KEY": "g",
        "BLUESKY_APP_PASSWORD": "p",
        "ANTHROPIC_API_KEY": "  sk-ant-x  ",
    }
    assert Settings.from_env(env).credentials.anthropic_api_key == "sk-ant-x"


def test_fallback_models_are_all_anthropic():
    """Guards against a Gemini id landing here, where it would skip discovery."""
    assert ANTHROPIC_MODELS
    assert all(is_anthropic_model(m) for m in ANTHROPIC_MODELS)


# ----------------------------------------------- per-model thinking-off config


def test_thinking_off_is_keyed_per_model_not_per_provider():
    """Haiku and Sonnet disagree, and getting it wrong is a 400 on every call."""
    assert thinking_off_for("claude-haiku-5-5") == {"type": "disabled"}
    assert thinking_off_for("claude-sonnet-5-5") == {"type": "between_tools"}


def test_thinking_off_is_case_insensitive():
    assert thinking_off_for("Claude-Sonnet-5-5") == {"type": "between_tools"}


def test_thinking_off_falls_back_for_an_unknown_model():
    """An unmapped model gets "disabled" - a loud 400 beats a silent budget leak."""
    assert thinking_off_for("claude-something-new") == {"type": "disabled"}


def test_generate_anthropic_sends_the_models_own_thinking_config(monkeypatch):
    rec = []
    monkeypatch.setitem(
        __import__("sys").modules, "anthropic", _fake_anthropic_module(rec)
    )
    generate_anthropic("key", "sys", "task", "claude-sonnet-5-5", 1500)
    assert rec[-1]["thinking"] == {"type": "between_tools"}, (
        "Sonnet rejects 'disabled' with a 400"
    )


# ------------------------------------------------------- chain composition


def test_chain_puts_anthropic_first_when_primary():
    assert compose_model_chain(["g1", "g2"], ["a1", "a2"], "primary", True) == [
        "a1",
        "a2",
        "g1",
        "g2",
    ]


def test_chain_puts_anthropic_last_when_fallback():
    assert compose_model_chain(["g1", "g2"], ["a1", "a2"], "fallback", True) == [
        "g1",
        "g2",
        "a1",
        "a2",
    ]


def test_chain_treats_an_unrecognised_position_as_fallback():
    """A typo must leave the proven Gemini path in charge, not hand over the feed."""
    assert compose_model_chain(["g1"], ["a1"], "primry", True) == ["g1", "a1"]


@pytest.mark.parametrize("position", ["primary", "fallback"])
def test_chain_is_unchanged_without_a_key(position):
    """No key is never a failed run, whatever the position says."""
    assert compose_model_chain(["g1", "g2"], ["a1"], position, False) == ["g1", "g2"]


def test_chain_is_unchanged_when_there_are_no_anthropic_models():
    assert compose_model_chain(["g1"], [], "primary", True) == ["g1"]


def test_chain_does_not_alias_its_inputs():
    """Returned list must not be the caller's, or a later append mutates config."""
    gemini = ["g1"]
    out = compose_model_chain(gemini, ["a1"], "fallback", True)
    out.append("mutated")
    assert gemini == ["g1"]


def test_configured_position_is_one_the_resolver_recognises():
    """Guards the revert switch: a typo silently degrades to fallback."""
    assert ANTHROPIC_CHAIN_POSITION in {"primary", "fallback"}


def test_the_anthropic_client_is_bounded(monkeypatch):
    """Cancelling an await does not stop a to_thread worker, and asyncio.run()
    waits for it at shutdown. Without an SDK deadline the pinned client's own
    defaults apply (600s, two retries), so a best-effort tag request could hold
    the process open long after the post shipped. Codex P1 on #194, which is
    what made the path routine: the tag proposer is an Anthropic model now.
    """
    rec = []
    monkeypatch.setitem(__import__("sys").modules, "anthropic", _fake_anthropic_module(rec))

    generate_anthropic("key", "sys", "task", "claude-haiku-5-5", 1500)

    init = rec[0]
    assert init["_init_timeout"] == llm.ANTHROPIC_REQUEST_TIMEOUT_SECONDS
    assert init["_init_max_retries"] == 0, (
        "SDK retries multiply the deadline without the model chain knowing")


def test_a_caller_can_impose_a_tighter_deadline(monkeypatch):
    """The tag pass has 20s for both its calls, so it passes its own budget."""
    rec = []
    monkeypatch.setitem(__import__("sys").modules, "anthropic", _fake_anthropic_module(rec))

    generate_anthropic("key", "sys", "task", "claude-haiku-5-5", 1500, 20.0)

    assert rec[0]["_init_timeout"] == 20.0

