"""Tests for the pioneer dimension (v4.15) — selector + state pruning."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from src import config
from src.agents import select_pioneer_topic
from src.utils import prune_pioneer_recent


# ── Test fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def patched_pools(monkeypatch):
    """Replace the global pioneer pools with a small, deterministic set so
    tests don't depend on the real list shifting underneath them."""
    monkeypatch.setattr(config, "PIONEER_DIMENSION_ENABLED", True)
    monkeypatch.setattr(config, "PIONEER_FALLBACK_PROBABILITY", 0.20)
    monkeypatch.setattr(config, "PIONEER_COOLDOWN_DAYS", 30)

    dated = [
        {"id": "test-dated-jan-1", "category": "pioneer",
         "month": 1, "day": 1, "year": 2000,
         "title": "January 1 anniversary", "detail": "..."},
        {"id": "test-dated-jul-4", "category": "artifact",
         "month": 7, "day": 4, "year": 1990,
         "title": "July 4 anniversary", "detail": "..."},
    ]
    undated = [
        {"id": "test-undated-a", "category": "pioneer", "title": "A", "detail": "..."},
        {"id": "test-undated-b", "category": "hero", "title": "B", "detail": "..."},
        {"id": "test-undated-c", "category": "project", "title": "C", "detail": "..."},
    ]

    # Patch in agents (which imported them at module load)
    from src import agents
    monkeypatch.setattr(agents, "PIONEER_EVENTS_DATED", dated)
    monkeypatch.setattr(agents, "PIONEER_FACTS_UNDATED", undated)
    monkeypatch.setattr(agents, "PIONEER_DIMENSION_ENABLED", True)
    monkeypatch.setattr(agents, "PIONEER_FALLBACK_PROBABILITY", 0.20)
    return dated, undated


def _seen(pioneer_recent=None):
    return {
        "links": [],
        "recent_topics": [],
        "pioneer_recent": pioneer_recent or [],
    }


def _now(month, day, year=2026):
    return datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)


# ── select_pioneer_topic ─────────────────────────────────────────────────────

def test_date_match_wins_over_probability(patched_pools):
    """A date-matched entry fires regardless of the probability roll."""
    # rng that always rolls high (probability gate would fail)
    rng = random.Random()
    rng.random = lambda: 0.99

    result = select_pioneer_topic(_seen(), now=_now(1, 1), rng=rng)
    assert result is not None
    assert result["pool"] == "dated"
    assert result["entry"]["id"] == "test-dated-jan-1"


def test_date_match_skipped_when_in_cooldown(patched_pools):
    """An anniversary still in cooldown does NOT fire — even if probability
    roll succeeds, we fall through to the undated pool."""
    rng = random.Random()
    rng.random = lambda: 0.0  # always within probability
    rng.choice = lambda items: items[0]

    seen = _seen([{
        "id": "test-dated-jan-1",
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }])
    result = select_pioneer_topic(seen, now=_now(1, 1), rng=rng)

    # Date match was blocked → fell through to undated
    assert result is not None
    assert result["pool"] == "undated"


def test_undated_picked_when_no_date_match_and_probability_passes(patched_pools):
    rng = random.Random()
    rng.random = lambda: 0.05  # well under 0.20
    rng.choice = lambda items: items[1]  # deterministic pick

    result = select_pioneer_topic(_seen(), now=_now(3, 14), rng=rng)
    assert result is not None
    assert result["pool"] == "undated"
    assert result["entry"]["id"] == "test-undated-b"


def test_no_pick_when_probability_fails_and_no_date_match(patched_pools):
    rng = random.Random()
    rng.random = lambda: 0.99  # over the 0.20 threshold

    assert select_pioneer_topic(_seen(), now=_now(3, 14), rng=rng) is None


def test_returns_none_when_all_undated_in_cooldown(patched_pools):
    """Probability gate passes but every undated entry is blocked → None."""
    rng = random.Random()
    rng.random = lambda: 0.0
    rng.choice = lambda items: items[0]

    cooldown = [
        {"id": "test-undated-a", "posted_at": datetime.now(timezone.utc).isoformat()},
        {"id": "test-undated-b", "posted_at": datetime.now(timezone.utc).isoformat()},
        {"id": "test-undated-c", "posted_at": datetime.now(timezone.utc).isoformat()},
    ]
    assert select_pioneer_topic(_seen(cooldown), now=_now(3, 14), rng=rng) is None


def test_disabled_flag_short_circuits(monkeypatch, patched_pools):
    """Feature flag off → always None even on a date match."""
    from src import agents
    monkeypatch.setattr(agents, "PIONEER_DIMENSION_ENABLED", False)
    rng = random.Random(); rng.random = lambda: 0.0
    assert select_pioneer_topic(_seen(), now=_now(1, 1), rng=rng) is None


def test_handles_missing_seen_data_gracefully(patched_pools):
    """seen_data=None should not crash; treated as empty cooldown."""
    rng = random.Random(); rng.random = lambda: 0.99
    result = select_pioneer_topic(None, now=_now(7, 4), rng=rng)
    assert result is not None and result["pool"] == "dated"


def test_handles_missing_pioneer_recent_key(patched_pools):
    """Pre-v4.15 state without pioneer_recent key still works."""
    rng = random.Random(); rng.random = lambda: 0.99
    seen = {"links": [], "recent_topics": []}  # no pioneer_recent
    result = select_pioneer_topic(seen, now=_now(7, 4), rng=rng)
    assert result is not None


# ── prune_pioneer_recent ─────────────────────────────────────────────────────

def test_prune_drops_entries_older_than_cooldown(monkeypatch):
    monkeypatch.setattr("src.state_store.PIONEER_COOLDOWN_DAYS", 30)
    now = datetime.now(timezone.utc)
    entries = [
        {"id": "fresh", "posted_at": (now - timedelta(days=5)).isoformat()},
        {"id": "stale", "posted_at": (now - timedelta(days=60)).isoformat()},
        {"id": "edge", "posted_at": (now - timedelta(days=29)).isoformat()},
    ]
    kept = prune_pioneer_recent(entries)
    ids = {e["id"] for e in kept}
    assert ids == {"fresh", "edge"}


def test_prune_tolerates_malformed_entries():
    entries = [
        "not-a-dict",
        {"id": "missing-posted-at"},
        {"posted_at": "missing-id"},
        {"id": "bad-date", "posted_at": "definitely-not-iso"},
        {"id": "good", "posted_at": datetime.now(timezone.utc).isoformat()},
    ]
    kept = prune_pioneer_recent(entries)
    assert kept == [{"id": "good", "posted_at": entries[-1]["posted_at"]}]


def test_prune_empty_list_is_empty():
    assert prune_pioneer_recent([]) == []
    assert prune_pioneer_recent(None) == []  # type: ignore[arg-type]
