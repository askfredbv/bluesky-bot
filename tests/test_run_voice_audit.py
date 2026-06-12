"""Tests for the monthly voice-audit entry point (scripts/run_voice_audit).

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


def test_auditor_models_are_pro_tier_not_the_flash_writer():
    """Independence guard: the auditor must differ from the gemini-3.5-flash
    writer and be Pro-tier."""
    for m in run_voice_audit._AUDITOR_MODELS:
        assert "flash" not in m            # not the writer's tier
        assert "pro" in m                  # Pro-tier critic


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
