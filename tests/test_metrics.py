"""Tests for src.metrics — feed health telemetry (Phase 1 Step 2)."""
from __future__ import annotations

import pytest

from src import metrics
from src.config import FEED_HEALTH_RECENT_ATTEMPTS_LIMIT
from src.metrics import FeedFetchResult, record_feed_attempt


# ---------------------------------------------------------------------------
# FeedFetchResult
# ---------------------------------------------------------------------------

def test_feed_fetch_result_defaults_entries_to_empty_list():
    r = FeedFetchResult(url="https://x", ok=True, entries_total=0, entries_accepted=0)
    assert r.entries == []
    assert r.error_type is None


def test_feed_fetch_result_preserves_passed_entries():
    r = FeedFetchResult(
        url="https://x",
        ok=True,
        entries_total=2,
        entries_accepted=1,
        entries=[{"title": "a"}],
    )
    assert r.entries == [{"title": "a"}]


# ---------------------------------------------------------------------------
# record_feed_attempt
# ---------------------------------------------------------------------------

def test_record_feed_attempt_creates_entry_for_new_url():
    health = {"feeds": {}}
    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=True, entries_total=5, entries_accepted=3),
    )
    assert "https://a" in health["feeds"]
    entry = health["feeds"]["https://a"]
    assert entry["last_fetch_at"] is not None
    assert entry["last_ok_at"] is not None
    assert entry["last_accepted_at"] is not None
    assert len(entry["recent_attempts"]) == 1
    assert entry["recent_attempts"][0]["ok"] is True
    assert entry["recent_attempts"][0]["accepted"] == 3


def test_record_feed_attempt_does_not_touch_last_ok_on_failure():
    health = {"feeds": {}}
    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=False, entries_total=0, entries_accepted=0, error_type="ReadTimeout"),
    )
    entry = health["feeds"]["https://a"]
    assert entry["last_fetch_at"] is not None
    assert entry["last_ok_at"] is None
    assert entry["last_accepted_at"] is None
    assert entry["recent_attempts"][0]["error"] == "ReadTimeout"


def test_record_feed_attempt_only_bumps_accepted_when_entries_survive():
    """ok=True but zero accepted (e.g. all entries older than lookback) must
    leave last_accepted_at at None."""
    health = {"feeds": {}}
    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=True, entries_total=10, entries_accepted=0),
    )
    entry = health["feeds"]["https://a"]
    assert entry["last_ok_at"] is not None
    assert entry["last_accepted_at"] is None


def test_record_feed_attempt_appends_without_overwriting_prior_success():
    """A failure after prior success must preserve last_ok_at / last_accepted_at."""
    health = {"feeds": {}}
    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=True, entries_total=5, entries_accepted=2),
    )
    previous_ok_at = health["feeds"]["https://a"]["last_ok_at"]
    previous_accepted_at = health["feeds"]["https://a"]["last_accepted_at"]

    record_feed_attempt(
        health,
        FeedFetchResult(url="https://a", ok=False, entries_total=0, entries_accepted=0, error_type="SSLError"),
    )
    entry = health["feeds"]["https://a"]
    assert entry["last_ok_at"] == previous_ok_at
    assert entry["last_accepted_at"] == previous_accepted_at
    assert len(entry["recent_attempts"]) == 2


def test_record_feed_attempt_trims_recent_attempts_to_limit():
    health = {"feeds": {}}
    for i in range(FEED_HEALTH_RECENT_ATTEMPTS_LIMIT + 5):
        record_feed_attempt(
            health,
            FeedFetchResult(url="https://a", ok=True, entries_total=i, entries_accepted=i),
        )
    attempts = health["feeds"]["https://a"]["recent_attempts"]
    assert len(attempts) == FEED_HEALTH_RECENT_ATTEMPTS_LIMIT
    # Oldest trimmed → most recent survive. accepted counts equal loop index,
    # so the surviving slice starts at index (total-limit) and ends at total-1.
    total = FEED_HEALTH_RECENT_ATTEMPTS_LIMIT + 5
    assert attempts[0]["accepted"] == total - FEED_HEALTH_RECENT_ATTEMPTS_LIMIT
    assert attempts[-1]["accepted"] == total - 1


# ---------------------------------------------------------------------------
# load_feed_health / save_feed_health
# ---------------------------------------------------------------------------

def test_load_feed_health_returns_default_when_no_gist_or_file(monkeypatch, tmp_path):
    monkeypatch.setattr("src.metrics.FEED_HEALTH_FILE", tmp_path / "missing.json")
    monkeypatch.setattr("src.utils._load_gist_state", lambda *_: None)
    assert metrics.load_feed_health() == {"feeds": {}}


def test_load_feed_health_prefers_gist(monkeypatch):
    payload = {"feeds": {"https://a": {"last_fetch_at": "2026-01-01T00:00:00+00:00"}}}
    monkeypatch.setattr("src.utils._load_gist_state", lambda name: payload if name == "feed_health.json" else None)
    assert metrics.load_feed_health() == payload


def test_save_feed_health_uses_gist_when_available(monkeypatch):
    calls = {}

    def fake_save_gist(name, data):
        calls["gist"] = (name, data)
        return True

    monkeypatch.setattr("src.utils._save_gist_state", fake_save_gist)
    metrics.save_feed_health({"feeds": {}})
    assert calls["gist"] == ("feed_health.json", {"feeds": {}})


def test_save_feed_health_falls_back_to_local_file(monkeypatch, tmp_path):
    monkeypatch.setattr("src.utils._save_gist_state", lambda *_: False)
    target = tmp_path / "feed_health.json"
    monkeypatch.setattr("src.metrics.FEED_HEALTH_FILE", target)

    metrics.save_feed_health({"feeds": {"https://a": {}}})
    assert target.exists()
    import json
    assert json.loads(target.read_text(encoding="utf-8")) == {"feeds": {"https://a": {}}}
