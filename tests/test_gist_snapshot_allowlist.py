"""Regression guard for the daily_post.yml Gist-snapshot allowlist.

The snapshot step uploads state to a PUBLIC Actions artifact. The allowlist is
the only boundary keeping pending_replies.json (drafted replies + the proactive
watchlist of named people) out of that public artifact. These tests fail loudly
if someone reverts to an unfiltered snapshot or adds the sensitive file back.
"""
from pathlib import Path

_WORKFLOW = (Path(__file__).resolve().parent.parent
             / ".github" / "workflows" / "daily_post.yml")
_SENSITIVE = "pending_replies.json"
_SAFE = ("seen_articles.json", "replied_to.json", "feed_health.json",
         "post_metrics.json", "growth.json")


def _text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def test_snapshot_is_gated_by_an_allowlist():
    text = _text()
    assert "SAFE_TO_SNAPSHOT" in text, "snapshot allowlist guard is missing"
    assert "if name not in SAFE_TO_SNAPSHOT" in text, (
        "snapshot no longer filters by the allowlist — it may upload every "
        "Gist file to the public artifact again")


def test_sensitive_file_is_never_snapshotted():
    assert _SENSITIVE not in _text(), (
        f"{_SENSITIVE} must never appear in the snapshot workflow — it would "
        "leak the proactive watchlist into a public artifact")


def test_known_safe_state_is_allowlisted():
    text = _text()
    for name in _SAFE:
        assert name in text, f"expected {name} in the snapshot allowlist"
