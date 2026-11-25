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
    assert "grounding reviewer: gemini-3.5-flash returned text" in out
    assert seen["reviewers"] and "gemini-3.7-flash" not in seen["reviewers"]
    assert agents._grounding_reviewers is original
