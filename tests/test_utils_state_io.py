import json
from pathlib import Path

from src import state_store


def _patch_state_files(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(state_store, "SEEN_FILE", tmp_path / "seen_articles.json")
    monkeypatch.setattr(state_store, "REPLIED_FILE", tmp_path / "replied_to.json")


def test_seen_articles_recovers_from_backup_when_primary_is_corrupt(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = state_store.SEEN_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    backup.write_text(json.dumps({"links": ["https://example.com"], "recent_topics": ["LLMs"]}))
    primary.write_text('{"links": ["incomplete"')

    loaded = state_store.load_seen_articles()

    # v4.15: load_seen_articles back-fills pioneer_recent on legacy state.
    assert loaded == {
        "links": ["https://example.com"],
        "recent_topics": ["LLMs"],
        "pioneer_recent": [],
    }


def test_seen_articles_preserves_corrupt_file_and_resets_when_no_valid_backup(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = state_store.SEEN_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    primary.write_text('{"broken": true')
    backup.write_text('{"also": "broken"')

    loaded = state_store.load_seen_articles()

    expected = {"links": [], "recent_topics": [], "pioneer_recent": []}
    assert loaded == expected
    assert primary.exists()
    assert json.loads(primary.read_text()) == expected
    # Corrupt file is saved with a timestamp suffix (e.g. seen_articles.json.corrupt.1234567890)
    corrupt_files = list(primary.parent.glob(primary.name + ".corrupt.*"))
    assert corrupt_files, "Expected a timestamped .corrupt.* file to exist"


def test_replied_to_recovers_from_interrupted_write(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = state_store.REPLIED_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    backup.write_text(json.dumps(["at://did:plc:1/post/1", "at://did:plc:2/post/2"]))
    primary.write_text('["at://did:plc:1/post/1",')

    loaded = state_store.load_replied_to()

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
    value, trusted = state_store._load_gist_state_strict("pending_replies.json")
    assert value is None
    assert trusted is True  # local dev — empty is legitimate


def test_strict_transport_failure_is_untrusted(monkeypatch):
    _patch_gist_env(monkeypatch)
    monkeypatch.setattr(
        state_store.httpx, "get",
        lambda *a, **k: _FakeResp(raise_exc=RuntimeError("503 Service Unavailable")),
    )
    value, trusted = state_store._load_gist_state_strict("pending_replies.json")
    assert value is None
    assert trusted is False  # read failed — state may exist, do NOT treat as empty


def test_strict_file_absent_is_trusted_empty(monkeypatch):
    _patch_gist_env(monkeypatch)
    # Gist reachable, but this file has never been written.
    monkeypatch.setattr(
        state_store.httpx, "get",
        lambda *a, **k: _FakeResp(payload={"files": {"other.json": {"content": "{}"}}}),
    )
    value, trusted = state_store._load_gist_state_strict("pending_replies.json")
    assert value is None
    assert trusted is True  # genuinely absent (first run) — safe empty


def test_strict_valid_content_is_trusted_value(monkeypatch):
    _patch_gist_env(monkeypatch)
    payload = {"files": {"pending_replies.json": {"content": json.dumps({"pending": [1]})}}}
    monkeypatch.setattr(state_store.httpx, "get", lambda *a, **k: _FakeResp(payload=payload))
    value, trusted = state_store._load_gist_state_strict("pending_replies.json")
    assert value == {"pending": [1]}
    assert trusted is True


def test_strict_corrupt_content_is_untrusted(monkeypatch):
    _patch_gist_env(monkeypatch)
    payload = {"files": {"pending_replies.json": {"content": '{"pending": [bad'}}}
    monkeypatch.setattr(state_store.httpx, "get", lambda *a, **k: _FakeResp(payload=payload))
    value, trusted = state_store._load_gist_state_strict("pending_replies.json")
    assert value is None
    assert trusted is False  # unparseable — don't clobber with empty


def test_load_gist_state_wrapper_still_returns_none_on_failure(monkeypatch):
    """The thin wrapper preserves the old 'None on any failure' contract."""
    _patch_gist_env(monkeypatch)
    monkeypatch.setattr(
        state_store.httpx, "get",
        lambda *a, **k: _FakeResp(raise_exc=RuntimeError("timeout")),
    )
    assert state_store._load_gist_state("pending_replies.json") is None




def _no_local_state(monkeypatch, tmp_path):
    """No Gist, no STATE_STORE, no local file -- a fresh Actions runner."""
    monkeypatch.setattr(state_store, "SEEN_FILE", tmp_path / "seen_articles.json")
    monkeypatch.delenv("STATE_STORE_URL", raising=False)


def test_strict_seen_read_is_untrusted_when_the_gist_is_unreachable(monkeypatch, tmp_path):
    """An unreachable Gist plus no fallback state is UNKNOWN, not empty.

    seen_articles.json is gitignored, so a fresh Actions runner has no local copy
    and STATE_STORE_URL is normally unset. One failed Gist GET therefore walks the
    whole chain to the empty default -- which must not be written back."""
    _no_local_state(monkeypatch, tmp_path)
    monkeypatch.setattr(state_store, "_load_gist_state_strict", lambda _f: (None, False))

    data, trusted = state_store.load_seen_articles_strict()

    assert data == {"links": [], "recent_topics": [], "pioneer_recent": []}
    assert trusted is False


def test_strict_seen_read_is_trusted_on_a_genuine_first_run(monkeypatch, tmp_path):
    """A reachable Gist with no file yet is a legitimate empty, and must stay
    writable -- otherwise the very first run could never persist anything."""
    _no_local_state(monkeypatch, tmp_path)
    monkeypatch.setattr(state_store, "_load_gist_state_strict", lambda _f: (None, True))

    data, trusted = state_store.load_seen_articles_strict()

    assert data == {"links": [], "recent_topics": [], "pioneer_recent": []}
    assert trusted is True


def test_update_seen_articles_skips_the_write_on_an_untrusted_read(monkeypatch, tmp_path):
    """Missing one run's bookkeeping beats erasing every seen link."""
    _no_local_state(monkeypatch, tmp_path)
    monkeypatch.setattr(state_store, "load_seen_articles_strict", lambda: ({"links": []}, False))
    saved, mutated = [], []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d))

    def mutator(current):
        mutated.append(current)
        return {"links": ["https://new"]}

    state_store.update_seen_articles(mutator)

    assert saved == [], "an untrusted read must not be written back"
    assert mutated == [], "the mutator must not even run"


def test_update_seen_articles_hands_the_mutator_the_fresh_read(monkeypatch, tmp_path):
    """The mutator receives the state read under the lock. Callers that ignore it
    and return an earlier snapshot defeat both the lock and the trust guard."""
    _no_local_state(monkeypatch, tmp_path)
    fresh = {"links": ["https://already-seen"], "recent_topics": [], "pioneer_recent": []}
    monkeypatch.setattr(state_store, "load_seen_articles_strict", lambda: (fresh, True))
    saved = []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d))

    state_store.update_seen_articles(lambda current: {**current, "links": current["links"] + ["https://new"]})

    assert saved == [{"links": ["https://already-seen", "https://new"], "recent_topics": [], "pioneer_recent": []}]


class _FakeWriteResp:
    """Response stub for the STATE_STORE_URL write path.

    Unlike _FakeResp this carries a status_code, because the write path decides
    on the status rather than on whether raise_for_status() threw — which is the
    whole point of the fix below.
    """

    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        # Mirrors httpx: 4xx/5xx raise, everything else (3xx included) does not.
        if 400 <= self.status_code < 600:
            raise RuntimeError(f"HTTP {self.status_code}")


def _patch_state_store_env(monkeypatch):
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    monkeypatch.delenv("STATE_STORE_TOKEN", raising=False)
    monkeypatch.delenv("GIST_ID", raising=False)  # skip the Gist tier entirely


def _record_calls(monkeypatch, put_status, post_status):
    calls = []

    def fake_put(*_a, **_k):
        calls.append("put")
        return _FakeWriteResp(put_status)

    def fake_post(*_a, **_k):
        calls.append("post")
        return _FakeWriteResp(post_status)

    monkeypatch.setattr(state_store.httpx, "put", fake_put)
    monkeypatch.setattr(state_store.httpx, "post", fake_post)
    return calls


def test_save_state_to_store_reports_a_stored_write(monkeypatch):
    _patch_state_store_env(monkeypatch)
    _record_calls(monkeypatch, put_status=200, post_status=500)
    assert state_store._save_state_to_store("seen_articles", {"links": []}) is True


def test_save_state_to_store_does_not_claim_success_on_a_redirect(monkeypatch):
    """A 3xx is not a stored write.

    httpx does not follow redirects by default and raise_for_status() only raises
    for 4xx/5xx, so the old `raise_for_status(); return True` branch reported
    success for a 302 -- a STATE_STORE_URL missing its trailing slash, or an
    http->https upgrade -- and the value was never written."""
    _patch_state_store_env(monkeypatch)
    calls = _record_calls(monkeypatch, put_status=302, post_status=302)

    assert state_store._save_state_to_store("seen_articles", {"links": []}) is False
    assert calls == ["put", "post"]  # the redirect fell through to the POST attempt


def test_save_state_to_store_does_not_claim_success_on_accepted(monkeypatch):
    """202 Accepted means queued, not stored. Strict on purpose: this return
    value gates the local-file fallback, so an optimistic True loses the state."""
    _patch_state_store_env(monkeypatch)
    _record_calls(monkeypatch, put_status=202, post_status=202)
    assert state_store._save_state_to_store("seen_articles", {"links": []}) is False


def test_save_state_to_store_falls_back_to_post_when_put_is_rejected(monkeypatch):
    """405 is the expected 'this endpoint wants POST' signal, not a failure."""
    _patch_state_store_env(monkeypatch)
    calls = _record_calls(monkeypatch, put_status=405, post_status=201)

    assert state_store._save_state_to_store("seen_articles", {"links": []}) is True
    assert calls == ["put", "post"]


def test_save_state_to_store_logs_write_failures_at_error(monkeypatch):
    """AGENTS.md principle 4: state-persistence failures log at ERROR, not WARN.
    Matches the v4.19 promotion already applied to _save_gist_state."""
    _patch_state_store_env(monkeypatch)
    _record_calls(monkeypatch, put_status=500, post_status=500)
    levels = []
    monkeypatch.setattr(state_store.SafeLogger, "error", lambda *a, **k: levels.append("error"))
    monkeypatch.setattr(state_store.SafeLogger, "warn", lambda *a, **k: levels.append("warn"))

    assert state_store._save_state_to_store("seen_articles", {"links": []}) is False
    assert "error" in levels
    assert "warn" not in levels


def test_save_seen_articles_still_writes_locally_when_the_remote_lies(monkeypatch, tmp_path):
    """The damage the phantom True actually did.

    save_seen_articles is a three-tier chain: Gist, then STATE_STORE_URL, then the
    local file. A wrong True from the remote tier returns early and skips the local
    write, so a redirect meant the state was persisted NOWHERE."""
    _patch_state_files(monkeypatch, tmp_path)
    _patch_state_store_env(monkeypatch)
    _record_calls(monkeypatch, put_status=302, post_status=302)

    seen = {"links": ["https://example.com/story"], "recent_topics": ["LLMs"], "pioneer_recent": []}
    state_store.save_seen_articles(seen)

    assert state_store.SEEN_FILE.exists(), "remote reported success it could not deliver"
    assert json.loads(state_store.SEEN_FILE.read_text()) == seen
