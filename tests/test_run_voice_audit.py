"""Tests for the voice-audit entry point (scripts/run_voice_audit).

Pure-logic + wiring only — no real API calls. The audit's value is human
judgement on the report; here we just verify it builds the prompt, picks an
auditor, and always exits 0 (best-effort).
"""

from scripts import run_voice_audit


def test_build_task_numbers_posts_and_includes_prompt():
    task = run_voice_audit._build_task(["first post", "second post"])
    assert "independent voice critic" in task
    assert "1. first post" in task
    assert "2. second post" in task


def test_auditor_models_are_not_the_writer():
    """Independence guard: no auditor may be a model the bot writes with, and
    none is a Flash or Lite tier."""
    from src.config import GEMINI_MODEL_PRIORITY

    for m in run_voice_audit._AUDITOR_MODELS:
        assert m not in GEMINI_MODEL_PRIORITY
        assert "flash" not in m and "lite" not in m


def test_main_exits_zero_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert run_voice_audit.main() == 0


def test_main_exits_zero_and_reports_when_feed_fails(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        run_voice_audit, "_fetch_recent_posts",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503")),
    )
    rc = run_voice_audit.main()
    assert rc == 0
    assert "could not run" in capsys.readouterr().out.lower()


def test_main_happy_path_emits_report_with_review_framing(monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(run_voice_audit, "_fetch_recent_posts",
                        lambda *a, **k: ["post one", "post two"])
    monkeypatch.setattr(run_voice_audit, "_run_auditor",
                        lambda *a, **k: ("gemini-3.5-pro", "Post 1 holds the voice."))

    rc = run_voice_audit.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert "Voice audit (gemini-3.5-pro)" in out
    # The anti-cargo-cult framing must be present — claims to verify, not verdicts.
    assert "verify" in out.lower()
    assert "not verdicts" in out.lower()
    assert "Post 1 holds the voice." in out


def _fake_genai(monkeypatch, finish_reason):
    from types import SimpleNamespace

    from google import genai

    seen = {}

    class _Models:
        def generate_content(self, model, contents, config):
            seen["config"] = config
            return SimpleNamespace(
                text="Post 1 holds the voice.",
                candidates=[SimpleNamespace(finish_reason=finish_reason)],
            )

    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace(models=_Models()))
    return seen


def test_run_auditor_marks_a_report_cut_off_at_the_output_limit(monkeypatch):
    """#297: a Pro model spent the 4096-token cap thinking and the issue body
    stopped mid-sentence, looking like a finished audit."""
    _fake_genai(monkeypatch, "FinishReason.MAX_TOKENS")
    _, text = run_voice_audit._run_auditor("k", "task")
    assert text.startswith("Post 1 holds the voice.")
    assert "Cut off" in text


def test_run_auditor_leaves_a_complete_report_alone_with_room_to_think(monkeypatch):
    seen = _fake_genai(monkeypatch, "FinishReason.STOP")
    _, text = run_voice_audit._run_auditor("k", "task")
    assert text == "Post 1 holds the voice."
    assert seen["config"]["max_output_tokens"] > 4096
