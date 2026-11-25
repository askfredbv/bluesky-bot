"""The per-model probe must report listed / attempted / passed separately, and
must restore the real generator whatever happens."""
import asyncio

import src.agents as agents
from scripts import probe_fallback as probe


def _run(coro):
    return asyncio.run(coro)


def test_failing_model_reports_attempts_and_error(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("400 thinking_budget not supported")

    async def fake_generate(key, recent, **kw):
        assert kw["model_priority"] == ["gemini-2.5-pro"]
        for _ in range(2):  # the real loop retries once per model
            try:
                agents._sync_generate(key, "i", "t", "gemini-2.5-pro")
            except RuntimeError:
                pass
        return [], "", None

    real = agents._sync_generate
    monkeypatch.setattr(agents, "_sync_generate", boom)
    monkeypatch.setattr(probe, "generate_content", fake_generate)
    assert _run(probe.one_model("k", "gemini-2.5-pro", {"gemini-2.5-pro"})) is False
    out = capsys.readouterr().out
    assert "listed=yes attempted=2 passed=no" in out
    assert "RuntimeError: 400 thinking_budget" in out
    # the wrapper must be unwound back to what was installed before the call
    assert agents._sync_generate is boom
    monkeypatch.setattr(agents, "_sync_generate", real)


def test_passing_model_and_unknown_listing(monkeypatch, capsys):
    monkeypatch.setattr(agents, "_sync_generate", lambda *a, **k: "ok")
    seen = {}

    async def fake_generate(key, recent, **kw):
        agents._sync_generate(key, "i", "t", "gemini-3.7-flash")
        agents._sync_generate(key, "i", "t", "gemini-3.5-flash")  # a reviewer
        # A one-model chain used to leave the grounding check with no reviewer
        # and reject every post; the probe must hand it a real one.
        seen["reviewers"] = agents._grounding_reviewers(
            "gemini-3.7-flash", ["gemini-3.7-flash"])
        return ["a post"], "t", "https://x"

    original = agents._grounding_reviewers
    monkeypatch.setattr(probe, "generate_content", fake_generate)
    assert _run(probe.one_model("k", "gemini-3.7-flash", None)) is True
    out = capsys.readouterr().out
    assert "listed=unknown attempted=1 passed=yes" in out  # reviewer not counted
    assert "grounding reviewer: gemini-3.5-flash returned 2 chars, ends 'ok'" in out
    assert seen["reviewers"] and "gemini-3.7-flash" not in seen["reviewers"]
    assert agents._grounding_reviewers is original


def test_models_that_normalize_to_nothing_is_a_failure(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["probe", "--models", " , ,"])
    assert _run(probe.main()) == 1
    assert "named no model ids" in capsys.readouterr().out


def test_runs_repeat_each_model_and_summarise(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(probe, "_listed_ids", lambda key: None)
    outcomes = iter([True, False, True, False, False, False])  # a: 2/3, b: 0/3

    async def fake_one_model(key, model, listed, akey=None, **kw):
        return next(outcomes)

    monkeypatch.setattr(probe, "one_model", fake_one_model)
    monkeypatch.setattr("sys.argv", ["probe", "--models", "a,b", "--runs", "3"])
    assert _run(probe.main()) == 1  # b never passed
    out = capsys.readouterr().out
    assert "  a: 2/3" in out and "  b: 0/3" in out


def test_partial_passes_are_not_a_failure(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(probe, "_listed_ids", lambda key: None)
    outcomes = iter([True, False])

    async def fake_one_model(key, model, listed, akey=None, **kw):
        return next(outcomes)

    monkeypatch.setattr(probe, "one_model", fake_one_model)
    monkeypatch.setattr("sys.argv", ["probe", "--models", "a", "--runs", "2"])
    assert _run(probe.main()) == 0


def test_zero_runs_is_rejected(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["probe", "--models", "a", "--runs", "0"])
    assert _run(probe.main()) == 1
    assert "at least 1" in capsys.readouterr().out


def test_zero_runs_is_rejected_in_the_outage_walk_too(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr("sys.argv", ["probe", "--runs", "0"])
    assert _run(probe.main()) == 1
    assert "at least 1" in capsys.readouterr().out


def test_repeated_ids_are_probed_once(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(probe, "_listed_ids", lambda key: None)
    seen = []

    async def fake_one_model(key, model, listed, akey=None, **kw):
        seen.append(model)
        return True

    monkeypatch.setattr(probe, "one_model", fake_one_model)
    monkeypatch.setattr("sys.argv", ["probe", "--models", "a, b,a", "--runs", "2"])
    assert _run(probe.main()) == 0
    assert seen == ["a", "a", "b", "b"]
    assert "  a: 2/2" in capsys.readouterr().out


def test_drop_thinking_removes_thinking_config_only_during_the_call(monkeypatch, capsys):
    monkeypatch.setattr(agents, "_sync_generate", lambda *a, **k: "ok")
    seen = {}

    def config():
        return agents._build_generate_kwargs("gemini-2.5-flash", "s", "t")["config"]

    async def fake_generate(key, recent, **kw):
        seen["during"] = "thinking_config" in config()
        return ["a post"], "t", "https://x"

    monkeypatch.setattr(probe, "generate_content", fake_generate)
    assert "thinking_config" in config()  # production shape, before
    assert _run(probe.one_model("k", "gemini-2.5-flash", None, drop_thinking=True)) is True
    assert seen["during"] is False
    assert "thinking_config" in config()  # restored, after
    assert "[thinking_config dropped]" in capsys.readouterr().out


def test_thinking_config_is_kept_by_default(monkeypatch):
    monkeypatch.setattr(agents, "_sync_generate", lambda *a, **k: "ok")
    seen = {}

    async def fake_generate(key, recent, **kw):
        seen["during"] = "thinking_config" in agents._build_generate_kwargs(
            "gemini-2.5-flash", "s", "t")["config"]
        return ["a post"], "t", "https://x"

    monkeypatch.setattr(probe, "generate_content", fake_generate)
    _run(probe.one_model("k", "gemini-2.5-flash", None))
    assert seen["during"] is True


def test_flag_reaches_one_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(probe, "_listed_ids", lambda key: None)
    got = []

    async def fake_one_model(key, model, listed, akey=None, drop_thinking=False):
        got.append(drop_thinking)
        return True

    monkeypatch.setattr(probe, "one_model", fake_one_model)
    monkeypatch.setattr("sys.argv", ["probe", "--models", "a", "--runs", "1", "--no-thinking-config"])
    assert _run(probe.main()) == 0
    assert got == [True]
