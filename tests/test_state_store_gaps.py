"""Behaviour of src/state_store.py the 2026-09-10 mutation run found unpinned
(repo-quality tip 6).

Each group names the surviving mutants it kills. Line numbers refer to
state_store.py at 2bfdcb9, unchanged since. Deliberately not pinned: the
timeout constant, JSON indentation, flush/fsync (durability a unit test cannot
observe) and the "moved" flag when a corrupt file cannot be renamed.
"""
import json

import pytest

from src import state_store

DEFAULT_SEEN = {"links": [], "recent_topics": [], "pioneer_recent": []}


class _Resp:
    """httpx-like response: 4xx/5xx raise on raise_for_status, as httpx does."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _events(monkeypatch):
    """Record the event name of every SafeLogger call."""
    seen = []
    for level in ("info", "warn", "error"):
        monkeypatch.setattr(state_store.SafeLogger, level, lambda event, *a, **k: seen.append(event))
    return seen


def _store_only(monkeypatch):
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    monkeypatch.delenv("STATE_STORE_TOKEN", raising=False)
    monkeypatch.delenv("GIST_ID", raising=False)


def _nothing_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("STATE_STORE_URL", raising=False)
    monkeypatch.delenv("GIST_ID", raising=False)
    monkeypatch.setattr(state_store, "SEEN_FILE", tmp_path / "seen_articles.json")
    monkeypatch.setattr(state_store, "REPLIED_FILE", tmp_path / "replied_to.json")


# ---------------------------------------------------------------------------
# STATE_STORE_URL: address, headers, reads (L30-L51)
# ---------------------------------------------------------------------------

def test_store_url_fills_a_key_template(monkeypatch):
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/{key}.json")
    assert state_store._state_store_url_for_key("seen_articles") == "https://state.example/seen_articles.json"


def test_store_url_appends_the_key_to_a_base_url(monkeypatch):
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot/")
    assert state_store._state_store_url_for_key("seen_articles") == "https://state.example/bot/seen_articles"


def test_store_token_is_sent_only_when_set(monkeypatch):
    monkeypatch.setenv("STATE_STORE_TOKEN", "t0k")
    assert state_store._state_store_headers()["Authorization"] == "Bearer t0k"
    monkeypatch.delenv("STATE_STORE_TOKEN")
    assert "Authorization" not in state_store._state_store_headers()


def test_store_read_without_a_url_makes_no_request(monkeypatch):
    monkeypatch.delenv("STATE_STORE_URL", raising=False)
    calls = []
    monkeypatch.setattr(state_store.httpx, "get", lambda *a, **k: calls.append(a) or _Resp(200, {"value": 1}))

    assert state_store._load_state_from_store("seen_articles") is None
    assert calls == []


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"value": ["a"]}, ["a"]),                # wrapped value is unwrapped
        (["a", "b"], ["a", "b"]),                 # a bare value is returned as is
        ({"links": ["a"]}, {"links": ["a"]}),     # a dict without "value" is not unwrapped
    ],
)
def test_store_read_unwraps_value_only_when_present(monkeypatch, payload, expected):
    _store_only(monkeypatch)
    monkeypatch.setattr(state_store.httpx, "get", lambda *a, **k: _Resp(200, payload))
    assert state_store._load_state_from_store("seen_articles") == expected


def test_store_read_404_means_no_state_yet_not_a_failure(monkeypatch):
    _store_only(monkeypatch)
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store.httpx, "get", lambda *a, **k: _Resp(404, {"value": "stale"}))

    assert state_store._load_state_from_store("seen_articles") is None
    assert events == []


def test_store_read_error_status_is_a_failed_read(monkeypatch):
    _store_only(monkeypatch)
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store.httpx, "get", lambda *a, **k: _Resp(500, {"value": ["stale"]}))

    assert state_store._load_state_from_store("seen_articles") is None
    assert "state_store_read_failed" in events


# ---------------------------------------------------------------------------
# STATE_STORE_URL: writes (L60-L132). The return value gates the local-file
# fallback, so a wrong True means the state is stored nowhere.
# ---------------------------------------------------------------------------

def test_store_write_without_a_url_reports_nothing_stored(monkeypatch):
    monkeypatch.delenv("STATE_STORE_URL", raising=False)
    calls = []
    monkeypatch.setattr(state_store.httpx, "put", lambda *a, **k: calls.append("put") or _Resp(200))
    monkeypatch.setattr(state_store.httpx, "post", lambda *a, **k: calls.append("post") or _Resp(200))

    assert state_store._save_state_to_store("seen_articles", {}) is False
    assert calls == []


def test_store_write_204_is_a_stored_write(monkeypatch):
    _store_only(monkeypatch)
    monkeypatch.setattr(state_store.httpx, "put", lambda *a, **k: _Resp(204))
    monkeypatch.setattr(state_store.httpx, "post", lambda *a, **k: _Resp(500))
    assert state_store._save_state_to_store("seen_articles", {}) is True


def test_store_put_405_falls_back_to_post_without_logging_a_failure(monkeypatch):
    _store_only(monkeypatch)
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store.httpx, "put", lambda *a, **k: _Resp(405))
    monkeypatch.setattr(state_store.httpx, "post", lambda *a, **k: _Resp(201))

    assert state_store._save_state_to_store("seen_articles", {}) is True
    assert events == []


def test_store_put_failure_is_logged_even_when_post_recovers(monkeypatch):
    _store_only(monkeypatch)
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store.httpx, "put", lambda *a, **k: _Resp(500))
    monkeypatch.setattr(state_store.httpx, "post", lambda *a, **k: _Resp(201))

    assert state_store._save_state_to_store("seen_articles", {}) is True
    assert events == ["state_store_put_failed"]


def test_store_write_reports_failure_when_the_post_raises(monkeypatch):
    _store_only(monkeypatch)

    def boom(*_a, **_k):
        raise ConnectionError("down")

    monkeypatch.setattr(state_store.httpx, "put", boom)
    monkeypatch.setattr(state_store.httpx, "post", boom)
    assert state_store._save_state_to_store("seen_articles", {}) is False


# ---------------------------------------------------------------------------
# Gist read token, the non-strict wrapper, and Gist saves (L159-L243)
# ---------------------------------------------------------------------------

def test_gist_read_sends_the_token(monkeypatch):
    monkeypatch.setenv("GIST_ID", "g1")
    monkeypatch.setenv("GIST_TOKEN", "gt")
    sent_headers = []
    monkeypatch.setattr(
        state_store.httpx, "get",
        lambda url, headers=None, **k: sent_headers.append(headers) or _Resp(200, {"files": {}}),
    )

    state_store._load_gist_state_strict("seen_articles.json")
    assert sent_headers[0]["Authorization"] == "Bearer gt"


def test_gist_wrapper_returns_the_value_on_success(monkeypatch):
    monkeypatch.setattr(state_store, "_load_gist_state_strict", lambda _f: ({"links": ["a"]}, True))
    assert state_store._load_gist_state("seen_articles.json") == {"links": ["a"]}


def test_gist_save_without_a_gist_id_reports_nothing_saved(monkeypatch):
    monkeypatch.delenv("GIST_ID", raising=False)
    calls = []
    monkeypatch.setattr(state_store.httpx, "patch", lambda *a, **k: calls.append(1) or _Resp(200))

    assert state_store._save_gist_state("seen_articles.json", {}) is False
    assert calls == []


def test_gist_save_sends_the_token_and_the_content(monkeypatch):
    monkeypatch.setenv("GIST_ID", "g1")
    monkeypatch.setenv("GIST_TOKEN", "gt")
    sent = []
    monkeypatch.setattr(
        state_store.httpx, "patch",
        lambda url, headers=None, json=None, **k: sent.append((url, headers, json)) or _Resp(200),
    )

    assert state_store._save_gist_state("seen_articles.json", {"links": ["a"]}) is True
    url, headers, body = sent[0]
    assert url.endswith("/gists/g1")
    assert headers["Authorization"] == "Bearer gt"
    assert json.loads(body["files"]["seen_articles.json"]["content"]) == {"links": ["a"]}


def test_gist_save_http_error_reports_failure(monkeypatch):
    monkeypatch.setenv("GIST_ID", "g1")
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store.httpx, "patch", lambda *a, **k: _Resp(500))

    assert state_store._save_gist_state("seen_articles.json", {}) is False
    assert "gist_state_save_failed" in events


# ---------------------------------------------------------------------------
# Atomic local writes (L246-L257)
# ---------------------------------------------------------------------------

def test_atomic_write_creates_missing_directories(tmp_path):
    target = tmp_path / "a" / "b" / "state.json"
    state_store._atomic_write_json(target, {"n": 1})
    state_store._atomic_write_json(target, {"n": 2})  # the directory now exists: must not fail
    assert json.loads(target.read_text()) == {"n": 2}


def test_atomic_write_leaves_no_temp_file_when_the_write_fails(tmp_path):
    target = tmp_path / "state.json"
    with pytest.raises(TypeError):
        state_store._atomic_write_json(target, {"not json": object()})
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Corruption-repairing loader (L271-L310)
# ---------------------------------------------------------------------------

def test_repair_loader_returns_the_default_for_a_missing_file(tmp_path):
    assert state_store._load_json_with_repair(tmp_path / "absent.json", lambda: {"d": 1}) == {"d": 1}


def test_repair_loader_returns_the_default_for_a_corrupt_file_without_backup(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken")
    assert state_store._load_json_with_repair(path, lambda: {"d": 1}) == {"d": 1}


def test_repair_loader_returns_the_default_when_the_file_cannot_be_read(tmp_path):
    path = tmp_path / "state.json"
    path.mkdir()  # it exists, but open() fails
    assert state_store._load_json_with_repair(path, lambda: {"d": 1}) == {"d": 1}


def test_repair_loader_migrates_a_legacy_list_and_rewrites_the_file(tmp_path):
    path = tmp_path / "seen_articles.json"
    path.write_text(json.dumps(["a", "b"]))
    migrated = {"links": ["a", "b"], "recent_topics": [], "pioneer_recent": []}

    assert state_store._load_json_with_repair(path, lambda: {}, migrate_list_to_seen_shape=True) == migrated
    assert json.loads(path.read_text()) == migrated


# ---------------------------------------------------------------------------
# Seen-state load chain (L359-L393)
# ---------------------------------------------------------------------------

def _seen_env(monkeypatch, tmp_path, gist=(None, True), remote=None):
    monkeypatch.setattr(state_store, "SEEN_FILE", tmp_path / "seen_articles.json")
    monkeypatch.setattr(state_store, "_load_gist_state_strict", lambda _f: gist)
    monkeypatch.setattr(state_store, "_load_state_from_store", lambda _k: remote)
    saved = []
    monkeypatch.setattr(state_store, "_save_state_to_store", lambda k, d: saved.append((k, d)) or True)
    return saved


def test_seen_read_takes_valid_gist_state_as_trusted(monkeypatch, tmp_path):
    _seen_env(monkeypatch, tmp_path, gist=({"links": ["a"], "recent_topics": ["LLMs"]}, True))
    assert state_store.load_seen_articles_strict() == (
        {"links": ["a"], "recent_topics": ["LLMs"], "pioneer_recent": []}, True,
    )


def test_seen_read_takes_valid_remote_state_as_trusted(monkeypatch, tmp_path):
    _seen_env(monkeypatch, tmp_path, remote={"links": ["r"], "recent_topics": []})
    assert state_store.load_seen_articles_strict() == (
        {"links": ["r"], "recent_topics": [], "pioneer_recent": []}, True,
    )


def test_seen_read_migrates_a_legacy_remote_list_and_writes_it_back(monkeypatch, tmp_path):
    saved = _seen_env(monkeypatch, tmp_path, remote=["a", "b"])
    migrated = {"links": ["a", "b"], "recent_topics": [], "pioneer_recent": []}

    assert state_store.load_seen_articles_strict() == (migrated, True)
    assert saved == [("seen_articles", migrated)]


def test_seen_read_migrates_a_legacy_local_list(monkeypatch, tmp_path):
    _seen_env(monkeypatch, tmp_path)
    state_store.SEEN_FILE.write_text(json.dumps(["a"]))
    assert state_store.load_seen_articles_strict() == (
        {"links": ["a"], "recent_topics": [], "pioneer_recent": []}, True,
    )


def test_seen_read_repairs_a_malformed_local_file_when_the_gist_was_read(monkeypatch, tmp_path):
    _seen_env(monkeypatch, tmp_path, gist=(None, True))
    state_store.SEEN_FILE.write_text(json.dumps({"unexpected": 1}))

    assert state_store.load_seen_articles_strict() == (DEFAULT_SEEN, True)
    assert json.loads(state_store.SEEN_FILE.read_text()) == DEFAULT_SEEN


def test_seen_read_leaves_a_malformed_local_file_alone_when_the_gist_failed(monkeypatch, tmp_path):
    _seen_env(monkeypatch, tmp_path, gist=(None, False))
    state_store.SEEN_FILE.write_text(json.dumps({"unexpected": 1}))

    assert state_store.load_seen_articles_strict() == (DEFAULT_SEEN, False)
    assert json.loads(state_store.SEEN_FILE.read_text()) == {"unexpected": 1}


# ---------------------------------------------------------------------------
# Replied-to load chain (L432-L454)
# ---------------------------------------------------------------------------

def _replied_env(monkeypatch, tmp_path, gist=(None, True), remote=None):
    monkeypatch.setattr(state_store, "REPLIED_FILE", tmp_path / "replied_to.json")
    monkeypatch.setattr(state_store, "_load_gist_state_strict", lambda _f: gist)
    monkeypatch.setattr(state_store, "_load_state_from_store", lambda _k: remote)


def test_replied_read_takes_a_gist_list_as_trusted(monkeypatch, tmp_path):
    _replied_env(monkeypatch, tmp_path, gist=(["at://1"], True))
    assert state_store.load_replied_to_strict() == (["at://1"], True)


def test_replied_read_takes_a_remote_list_as_trusted(monkeypatch, tmp_path):
    _replied_env(monkeypatch, tmp_path, remote=["at://r"])
    assert state_store.load_replied_to_strict() == (["at://r"], True)


def test_replied_read_repairs_a_malformed_local_file_when_the_gist_was_read(monkeypatch, tmp_path):
    _replied_env(monkeypatch, tmp_path, gist=(None, True))
    state_store.REPLIED_FILE.write_text(json.dumps({"unexpected": 1}))

    assert state_store.load_replied_to_strict() == ([], True)
    assert json.loads(state_store.REPLIED_FILE.read_text()) == []


def test_replied_read_leaves_a_malformed_local_file_alone_when_the_gist_failed(monkeypatch, tmp_path):
    _replied_env(monkeypatch, tmp_path, gist=(None, False))
    state_store.REPLIED_FILE.write_text(json.dumps({"unexpected": 1}))

    assert state_store.load_replied_to_strict() == ([], False)
    assert json.loads(state_store.REPLIED_FILE.read_text()) == {"unexpected": 1}


# ---------------------------------------------------------------------------
# Save fallbacks (L412, L458-L464)
# ---------------------------------------------------------------------------

def test_save_seen_writes_the_backup_and_the_file_when_no_remote_takes_it(monkeypatch, tmp_path):
    _nothing_configured(monkeypatch, tmp_path)
    data = {"links": ["a"], "recent_topics": [], "pioneer_recent": []}

    state_store.save_seen_articles(data)
    assert json.loads(state_store.SEEN_FILE.read_text()) == data
    assert json.loads(state_store.SEEN_FILE.with_suffix(".json.bak").read_text()) == data


@pytest.mark.parametrize("tier", ["gist", "store"])
def test_save_replied_stops_at_the_first_tier_that_stores_it(monkeypatch, tmp_path, tier):
    _nothing_configured(monkeypatch, tmp_path)
    monkeypatch.setattr(state_store, "_save_gist_state", lambda _f, _d: tier == "gist")
    monkeypatch.setattr(state_store, "_save_state_to_store", lambda _k, _d: tier == "store")

    state_store.save_replied_to(["at://1"])
    assert not state_store.REPLIED_FILE.exists()


def test_save_replied_writes_the_backup_and_the_file_when_no_remote_takes_it(monkeypatch, tmp_path):
    _nothing_configured(monkeypatch, tmp_path)

    state_store.save_replied_to(["at://1"])
    assert json.loads(state_store.REPLIED_FILE.read_text()) == ["at://1"]
    assert json.loads(state_store.REPLIED_FILE.with_suffix(".json.bak").read_text()) == ["at://1"]


# ---------------------------------------------------------------------------
# update_* return values and the replied-to save (L488-L515)
# ---------------------------------------------------------------------------

def test_update_seen_returns_the_read_when_it_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "SEEN_FILE", tmp_path / "seen_articles.json")
    current = {"links": ["x"], "recent_topics": [], "pioneer_recent": []}
    monkeypatch.setattr(state_store, "load_seen_articles_strict", lambda: (current, False))
    assert state_store.update_seen_articles(lambda _c: {"links": []}) is current


def test_update_seen_returns_what_it_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "SEEN_FILE", tmp_path / "seen_articles.json")
    monkeypatch.setattr(state_store, "load_seen_articles_strict", lambda: ({"links": []}, True))
    monkeypatch.setattr(state_store, "save_seen_articles", lambda _d: None)
    assert state_store.update_seen_articles(lambda _c: {"links": ["new"]}) == {"links": ["new"]}


def test_update_replied_saves_and_returns_the_mutated_list(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "REPLIED_FILE", tmp_path / "replied_to.json")
    monkeypatch.setattr(state_store, "load_replied_to_strict", lambda: (["at://old"], True))
    saved = []
    monkeypatch.setattr(state_store, "save_replied_to", lambda d: saved.append(d))

    assert state_store.update_replied_to(lambda c: c + ["at://new"]) == ["at://old", "at://new"]
    assert saved == [["at://old", "at://new"]]


def test_update_replied_returns_the_read_when_it_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "REPLIED_FILE", tmp_path / "replied_to.json")
    monkeypatch.setattr(state_store, "load_replied_to_strict", lambda: (["at://old"], False))
    assert state_store.update_replied_to(lambda _c: []) == ["at://old"]
