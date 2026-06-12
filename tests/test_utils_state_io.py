import json
from pathlib import Path

from src import utils


def _patch_state_files(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(utils, "SEEN_FILE", tmp_path / "seen_articles.json")
    monkeypatch.setattr(utils, "REPLIED_FILE", tmp_path / "replied_to.json")


def test_seen_articles_recovers_from_backup_when_primary_is_corrupt(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = utils.SEEN_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    backup.write_text(json.dumps({"links": ["https://example.com"], "recent_topics": ["LLMs"]}))
    primary.write_text('{"links": ["incomplete"')

    loaded = utils.load_seen_articles()

    # v4.15: load_seen_articles back-fills pioneer_recent on legacy state.
    assert loaded == {
        "links": ["https://example.com"],
        "recent_topics": ["LLMs"],
        "pioneer_recent": [],
    }


def test_seen_articles_preserves_corrupt_file_and_resets_when_no_valid_backup(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = utils.SEEN_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    primary.write_text('{"broken": true')
    backup.write_text('{"also": "broken"')

    loaded = utils.load_seen_articles()

    expected = {"links": [], "recent_topics": [], "pioneer_recent": []}
    assert loaded == expected
    assert primary.exists()
    assert json.loads(primary.read_text()) == expected
    # Corrupt file is saved with a timestamp suffix (e.g. seen_articles.json.corrupt.1234567890)
    corrupt_files = list(primary.parent.glob(primary.name + ".corrupt.*"))
    assert corrupt_files, "Expected a timestamped .corrupt.* file to exist"


def test_replied_to_recovers_from_interrupted_write(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = utils.REPLIED_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    backup.write_text(json.dumps(["at://did:plc:1/post/1", "at://did:plc:2/post/2"]))
    primary.write_text('["at://did:plc:1/post/1",')

    loaded = utils.load_replied_to()

    assert loaded == ["at://did:plc:1/post/1", "at://did:plc:2/post/2"]
    assert json.loads(primary.read_text()) == loaded


# ---------------------------------------------------------------------------
# _load_gist_state_strict — trustworthy-empty vs untrusted-empty
# (Codex review 2026-06-12: a failed read must be distinguishable from
#  genuinely-absent state, so callers don't overwrite real data.)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload=None, raise_exc=None):
        self._payload = payload
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self):
        return self._payload


def _patch_gist_env(monkeypatch):
    monkeypatch.setenv("GIST_ID", "fake-gist-id")
    monkeypatch.setenv("GIST_TOKEN", "fake-token")


def test_strict_no_gist_configured_is_trusted_empty(monkeypatch):
    monkeypatch.delenv("GIST_ID", raising=False)
    value, trusted = utils._load_gist_state_strict("pending_replies.json")
    assert value is None
    assert trusted is True  # local dev — empty is legitimate


def test_strict_transport_failure_is_untrusted(monkeypatch):
    _patch_gist_env(monkeypatch)
    monkeypatch.setattr(
        utils.httpx, "get",
        lambda *a, **k: _FakeResp(raise_exc=RuntimeError("503 Service Unavailable")),
    )
    value, trusted = utils._load_gist_state_strict("pending_replies.json")
    assert value is None
    assert trusted is False  # read failed — state may exist, do NOT treat as empty


def test_strict_file_absent_is_trusted_empty(monkeypatch):
    _patch_gist_env(monkeypatch)
    # Gist reachable, but this file has never been written.
    monkeypatch.setattr(
        utils.httpx, "get",
        lambda *a, **k: _FakeResp(payload={"files": {"other.json": {"content": "{}"}}}),
    )
    value, trusted = utils._load_gist_state_strict("pending_replies.json")
    assert value is None
    assert trusted is True  # genuinely absent (first run) — safe empty


def test_strict_valid_content_is_trusted_value(monkeypatch):
    _patch_gist_env(monkeypatch)
    payload = {"files": {"pending_replies.json": {"content": json.dumps({"pending": [1]})}}}
    monkeypatch.setattr(utils.httpx, "get", lambda *a, **k: _FakeResp(payload=payload))
    value, trusted = utils._load_gist_state_strict("pending_replies.json")
    assert value == {"pending": [1]}
    assert trusted is True


def test_strict_corrupt_content_is_untrusted(monkeypatch):
    _patch_gist_env(monkeypatch)
    payload = {"files": {"pending_replies.json": {"content": '{"pending": [bad'}}}
    monkeypatch.setattr(utils.httpx, "get", lambda *a, **k: _FakeResp(payload=payload))
    value, trusted = utils._load_gist_state_strict("pending_replies.json")
    assert value is None
    assert trusted is False  # unparseable — don't clobber with empty


def test_load_gist_state_wrapper_still_returns_none_on_failure(monkeypatch):
    """The thin wrapper preserves the old 'None on any failure' contract."""
    _patch_gist_env(monkeypatch)
    monkeypatch.setattr(
        utils.httpx, "get",
        lambda *a, **k: _FakeResp(raise_exc=RuntimeError("timeout")),
    )
    assert utils._load_gist_state("pending_replies.json") is None


