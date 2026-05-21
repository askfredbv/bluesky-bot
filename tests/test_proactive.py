"""Tests for the Phase 4b proactive-reply pipeline.

  - Commit 1: state schema + load/save helpers in ``src/proactive.py``
  - Commit 2: ``generate_proactive_reply`` + voice validator in ``src/agents.py``
  - Commit 3: scan / filter / pick / stage in ``src/proactive.py``

Approve / reject flow lands in commit 4.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src import agents, proactive
from src.agents import (
    _is_skip_response,
    _validate_proactive_reply,
    generate_proactive_reply,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers for commit-3 tests
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 21, 18, 0, tzinfo=timezone.utc)


def _make_feed_item(
    *,
    text="cgroup v1 counts page cache against the memory limit; v2 separates them.",
    hours_ago=2,
    likes=3,
    reposts=1,
    replies=1,
    uri="at://did:plc:fakebsky/app.bsky.feed.post/xyz",
    is_repost=False,
    is_reply=False,
    embed=None,
    now=_NOW,
):
    """Build a fake atproto FeedViewPost-shaped object for filter tests.

    Mirrors the attribute names the SDK exposes: ``item.post.record.*``,
    ``item.reason`` (repost marker), etc. Defaults pass every filter rule,
    so each test mutates only the field it's exercising.
    """
    ts = (now - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    record = SimpleNamespace(
        text=text,
        created_at=ts,
        reply=SimpleNamespace() if is_reply else None,
        embed=embed,
    )
    post = SimpleNamespace(
        uri=uri,
        indexed_at=ts,
        like_count=likes,
        repost_count=reposts,
        reply_count=replies,
        record=record,
    )
    return SimpleNamespace(post=post, reason=SimpleNamespace() if is_repost else None)


class _FakeBskyClient:
    """Minimal AsyncClient stand-in for scan tests.

    Mirrors ``client.app.bsky.feed.get_author_feed({"actor": handle, ...})``.
    Constructed with a ``feed_by_handle`` dict mapping handle → list of
    feed items. Unknown handles return empty feeds.
    """

    def __init__(self, feed_by_handle=None, raises_for=None):
        self._feed_by_handle = feed_by_handle or {}
        self._raises_for = raises_for or set()
        self.calls: list[str] = []
        self.app = SimpleNamespace(bsky=SimpleNamespace(feed=self))

    async def get_author_feed(self, params):
        handle = params["actor"]
        self.calls.append(handle)
        if handle in self._raises_for:
            raise RuntimeError(f"fetch failed for {handle}")
        return SimpleNamespace(feed=self._feed_by_handle.get(handle, []))


# ---------------------------------------------------------------------------
# load_pending_replies
# ---------------------------------------------------------------------------

def test_load_returns_empty_skeleton_when_gist_absent(monkeypatch):
    """No state on the Gist → canonical empty skeleton with all three buckets."""
    monkeypatch.setattr(proactive, "_load_gist_state", lambda _f: None)

    state = proactive.load_pending_replies()

    assert state == {"pending": [], "posted": [], "rejected": []}


def test_load_returns_state_when_gist_present(monkeypatch):
    """Gist has full state → returned verbatim."""
    fixture = {
        "pending": [{"id": "abc", "platform": "bluesky", "draft_reply": "hi"}],
        "posted": [{"id": "old", "posted_at": "2026-05-14T10:00:00Z"}],
        "rejected": [{"id": "bad", "rejected_reason": "voice_drift"}],
    }
    monkeypatch.setattr(proactive, "_load_gist_state", lambda _f: fixture)

    state = proactive.load_pending_replies()

    assert state == fixture


def test_load_fills_missing_buckets_with_empty_lists(monkeypatch):
    """Partial state (only 'pending' present) → other buckets default to []."""
    monkeypatch.setattr(
        proactive, "_load_gist_state",
        lambda _f: {"pending": [{"id": "abc"}]},
    )

    state = proactive.load_pending_replies()

    assert state["pending"] == [{"id": "abc"}]
    assert state["posted"] == []
    assert state["rejected"] == []


def test_load_ignores_non_list_bucket_values(monkeypatch):
    """Defensive: malformed bucket (string instead of list) → empty list."""
    monkeypatch.setattr(
        proactive, "_load_gist_state",
        lambda _f: {"pending": "not-a-list", "posted": [], "rejected": []},
    )

    state = proactive.load_pending_replies()

    assert state == {"pending": [], "posted": [], "rejected": []}


def test_load_handles_non_dict_state(monkeypatch):
    """Defensive: Gist returns a list (corrupt) → empty skeleton."""
    monkeypatch.setattr(proactive, "_load_gist_state", lambda _f: ["unexpected"])

    state = proactive.load_pending_replies()

    assert state == {"pending": [], "posted": [], "rejected": []}


def test_load_handles_gist_exception(monkeypatch):
    """Gist read raises → empty skeleton, no crash."""
    def boom(_f):
        raise RuntimeError("Gist unreachable")
    monkeypatch.setattr(proactive, "_load_gist_state", boom)

    state = proactive.load_pending_replies()

    assert state == {"pending": [], "posted": [], "rejected": []}


# ---------------------------------------------------------------------------
# save_pending_replies
# ---------------------------------------------------------------------------

def test_save_persists_state_and_returns_true(monkeypatch):
    """Save delegates to _save_gist_state and returns its result."""
    captured = {}
    def fake_save(filename, data):
        captured["filename"] = filename
        captured["data"] = data
        return True
    monkeypatch.setattr(proactive, "_save_gist_state", fake_save)

    state = {"pending": [{"id": "abc"}], "posted": [], "rejected": []}
    ok = proactive.save_pending_replies(state)

    assert ok is True
    assert captured["filename"] == "pending_replies.json"
    assert captured["data"] == state


def test_save_returns_false_when_gist_save_returns_false(monkeypatch):
    """If the underlying Gist save returns False, propagate it."""
    monkeypatch.setattr(proactive, "_save_gist_state", lambda _f, _d: False)

    ok = proactive.save_pending_replies(proactive._empty_state())

    assert ok is False


def test_save_returns_false_on_exception(monkeypatch):
    """Exception in Gist save → False, no propagation."""
    def boom(_f, _d):
        raise RuntimeError("Gist write failed")
    monkeypatch.setattr(proactive, "_save_gist_state", boom)

    ok = proactive.save_pending_replies(proactive._empty_state())

    assert ok is False


# ---------------------------------------------------------------------------
# new_draft_id
# ---------------------------------------------------------------------------

def test_new_draft_id_returns_unique_strings():
    """UUIDs should not collide across consecutive calls."""
    a = proactive.new_draft_id()
    b = proactive.new_draft_id()

    assert isinstance(a, str)
    assert isinstance(b, str)
    assert a != b
    assert len(a) == 36  # canonical UUID4 string length


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------

def test_empty_state_has_three_buckets():
    """The state contract is exactly these three keys, no others."""
    state = proactive._empty_state()

    assert set(state.keys()) == {"pending", "posted", "rejected"}
    assert all(isinstance(v, list) for v in state.values())


def test_empty_state_returns_fresh_dict():
    """Sanity: callers can mutate without affecting future loads."""
    a = proactive._empty_state()
    b = proactive._empty_state()
    a["pending"].append({"id": "x"})

    assert b["pending"] == []


# ===========================================================================
# Commit 2 — _is_skip_response / _validate_proactive_reply / generate_proactive_reply
# ===========================================================================

# ---------------------------------------------------------------------------
# _is_skip_response
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["SKIP", "skip", " SKIP ", "SKIP.", "skip!", "Skip,", ""])
def test_is_skip_response_detects_skip_literal(text):
    """The SKIP literal — with surrounding whitespace / trailing punctuation — must be detected."""
    assert _is_skip_response(text) is True


@pytest.mark.parametrize("text", [
    "I'll skip this one",                     # narration about skipping ≠ a SKIP signal
    "Skip — nothing to add here",
    "Let me skip that",
    "Actually here's a thought: SKIP that",
    "A real reply that mentions skip in passing.",
])
def test_is_skip_response_does_not_match_skip_inside_longer_text(text):
    """SKIP must be the WHOLE response — not a substring of an explanation."""
    assert _is_skip_response(text) is False


# ---------------------------------------------------------------------------
# _validate_proactive_reply
# ---------------------------------------------------------------------------

def test_validate_accepts_clean_short_reply():
    ok, _ = _validate_proactive_reply(
        "cgroup v1 counts page cache against the memory limit; v2 separates them."
    )
    assert ok is True


def test_validate_rejects_empty():
    ok, reason = _validate_proactive_reply("")
    assert ok is False
    assert "empty" in reason.lower()


def test_validate_rejects_too_short():
    """Under MIN_CHARS the reply can't be doing info-add work."""
    ok, reason = _validate_proactive_reply("Cool point.")
    assert ok is False
    assert "short" in reason.lower()


def test_validate_rejects_over_length():
    """> PROACTIVE_REPLY_MAX_CHARS is rejected even if voice-clean."""
    long_reply = "a" * 250
    ok, reason = _validate_proactive_reply(long_reply)
    assert ok is False
    assert "exceeds" in reason.lower()


def test_validate_rejects_banned_hype():
    """Hype in a reply is more grating than in a broadcast — hard reject."""
    ok, reason = _validate_proactive_reply(
        "This is absolutely game-changing for the field of structured outputs."
    )
    assert ok is False
    assert "hype" in reason.lower()


def test_validate_rejects_banned_opener():
    """Source-summary openers ('Great point about X') burn credibility."""
    ok, reason = _validate_proactive_reply(
        "Tool Tuesday: ripgrep is faster than grep for codebase-wide searches."
    )
    assert ok is False
    assert "opener" in reason.lower()


def test_validate_rejects_reader_bait_ending():
    """Reader-bait questions at the end pull replies into the wrong register."""
    ok, reason = _validate_proactive_reply(
        "cgroup v2 fixes the page-cache accounting bug from v1. What do you think?"
    )
    assert ok is False
    assert "reader-bait" in reason.lower()


def test_validate_rejects_teaser_ending():
    """Broken-promise teasers in a reply context are especially bad — no follow-up exists."""
    ok, reason = _validate_proactive_reply(
        "Anthropic's tool-call enum constraints catch most hallucinations — more soon."
    )
    assert ok is False
    assert "teaser" in reason.lower()


def test_validate_rejects_repetitive_gibberish():
    """Repeated-char runs indicate model malfunction."""
    ok, reason = _validate_proactive_reply("aaaaaaaaaaaaaaaaa is the best approach here.")
    assert ok is False
    assert "gibberish" in reason.lower() or "repetitive" in reason.lower()


# ---------------------------------------------------------------------------
# generate_proactive_reply — orchestrator (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_returns_clean_reply_on_happy_path(monkeypatch):
    """Model produces a voice-clean reply → returned verbatim (post-cleanup)."""
    monkeypatch.setattr(
        agents, "_sync_generate",
        lambda *_a, **_kw:
        "cgroup v1 counts page cache against the memory limit; v2 separates them. "
        "If you're on v1 that 500MB gap is page cache the kernel won't evict.",
    )

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="xeiaso.net",
        parent_text="My pod kept OOMKilling at 1.5GB despite a 2GB limit.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is not None
    assert "cgroup v1" in result
    assert len(result) <= 200


@pytest.mark.asyncio
async def test_generate_returns_none_on_skip_literal(monkeypatch):
    """Model returns SKIP → None (the common case, not an error)."""
    monkeypatch.setattr(agents, "_sync_generate", lambda *_a, **_kw: "SKIP")

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="Excited to announce we're hiring.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is None


@pytest.mark.asyncio
async def test_generate_strips_quote_artifacts(monkeypatch):
    """Model wraps reply in quotes despite the prompt → strip them before validating."""
    monkeypatch.setattr(
        agents, "_sync_generate",
        lambda *_a, **_kw:
        '"pandas.read_html does this in one line but trips on rowspan; '
        'flagging that helps."',
    )

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="Built an HTML→CSV tool.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is not None
    assert not result.startswith('"')
    assert not result.endswith('"')


@pytest.mark.asyncio
async def test_generate_strips_reply_prefix_artifact(monkeypatch):
    """Model echoes the 'Reply:' label from the few-shot block → strip before validating."""
    monkeypatch.setattr(
        agents, "_sync_generate",
        lambda *_a, **_kw:
        "Reply: pandas.read_html does this in one line but trips on rowspan/colspan.",
    )

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="Built an HTML→CSV tool.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is not None
    assert not result.lower().startswith("reply:")


@pytest.mark.asyncio
async def test_generate_returns_none_when_all_attempts_fail_validation(monkeypatch):
    """Every model attempt returns voice-failing output → None, not garbage."""
    monkeypatch.setattr(
        agents, "_sync_generate",
        lambda *_a, **_kw: "This is absolutely game-changing news for the field.",
    )

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="xeiaso.net",
        parent_text="Some technical observation.",
        model_priority=["gemini-2.5-pro", "gemini-2.5-flash"],
    )

    assert result is None


@pytest.mark.asyncio
async def test_generate_returns_none_when_model_chain_exhausts(monkeypatch):
    """API errors on every model → None, no propagation."""
    def boom(*_a, **_kw):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(agents, "_sync_generate", boom)

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="A post.",
        model_priority=["gemini-2.5-pro", "gemini-2.5-flash"],
    )

    assert result is None


@pytest.mark.asyncio
async def test_generate_retries_within_model_on_validation_failure(monkeypatch):
    """First attempt fails validation, second attempt succeeds → return the second."""
    calls = {"count": 0}

    def stub(*_a, **_kw):
        calls["count"] += 1
        if calls["count"] == 1:
            return "This is absolutely amazing news here."  # banned hype → fails
        return "cgroup v1 counts page cache against memory limits; v2 separates them."

    monkeypatch.setattr(agents, "_sync_generate", stub)

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="xeiaso.net",
        parent_text="OOMKill at 1.5GB.",
        model_priority=["gemini-2.5-pro"],
    )

    assert result is not None
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_generate_falls_through_to_next_model_on_api_error(monkeypatch):
    """First model raises (API error), second model succeeds → return second model's output."""
    calls = {"models_seen": []}

    def stub(_api_key, _instr, _task, model):
        calls["models_seen"].append(model)
        if model == "gemini-2.5-pro":
            raise RuntimeError("quota exhausted")
        return "Postfix releases since 1998 have shipped with TLS off by default for compatibility — the change is opt-in."

    monkeypatch.setattr(agents, "_sync_generate", stub)

    result = await generate_proactive_reply(
        api_key="fake-key",
        parent_author="simonwillison.net",
        parent_text="Notes on email server config.",
        model_priority=["gemini-2.5-pro", "gemini-2.5-flash"],
    )

    assert result is not None
    assert calls["models_seen"][0] == "gemini-2.5-pro"
    assert "gemini-2.5-flash" in calls["models_seen"]


# ===========================================================================
# Commit 3 — expire / cooldown / filter / scan / pick / stage
# ===========================================================================

# ---------------------------------------------------------------------------
# expire_old_drafts
# ---------------------------------------------------------------------------

def test_expire_moves_drafts_older_than_24h_to_rejected():
    """A pending draft older than the expiry threshold → rejected with reason 'expired'."""
    old_ts = (_NOW - timedelta(hours=30)).isoformat()
    state = {
        "pending": [{"id": "old", "parent_author": "simonwillison.net", "generated_at": old_ts}],
        "posted": [],
        "rejected": [],
    }

    moved = proactive.expire_old_drafts(state, _NOW)

    assert moved == 1
    assert state["pending"] == []
    assert len(state["rejected"]) == 1
    assert state["rejected"][0]["rejected_reason"] == "expired"
    assert state["rejected"][0]["id"] == "old"


def test_expire_leaves_fresh_drafts_alone():
    """Pending drafts within the expiry window stay in pending."""
    fresh_ts = (_NOW - timedelta(hours=5)).isoformat()
    state = {
        "pending": [{"id": "fresh", "parent_author": "xeiaso.net", "generated_at": fresh_ts}],
        "posted": [],
        "rejected": [],
    }

    moved = proactive.expire_old_drafts(state, _NOW)

    assert moved == 0
    assert len(state["pending"]) == 1
    assert state["rejected"] == []


def test_expire_treats_malformed_timestamp_as_expired():
    """A draft missing/malformed generated_at can't be safely kept → reject as expired."""
    state = {
        "pending": [{"id": "broken", "parent_author": "x", "generated_at": "not-a-date"}],
        "posted": [],
        "rejected": [],
    }

    moved = proactive.expire_old_drafts(state, _NOW)

    assert moved == 1
    assert state["rejected"][0]["rejected_reason"] == "expired"


# ---------------------------------------------------------------------------
# is_handle_in_cooldown
# ---------------------------------------------------------------------------

def test_cooldown_true_when_recent_posted_entry_for_handle():
    """A posted reply within 7 days → handle is in cooldown."""
    state = {
        "pending": [], "rejected": [],
        "posted": [{
            "parent_author": "simonwillison.net",
            "posted_at": (_NOW - timedelta(days=2)).isoformat(),
        }],
    }

    assert proactive.is_handle_in_cooldown("simonwillison.net", state, _NOW) is True


def test_cooldown_false_when_posted_entry_outside_window():
    """A posted reply > 7 days ago → handle is clear."""
    state = {
        "pending": [], "rejected": [],
        "posted": [{
            "parent_author": "simonwillison.net",
            "posted_at": (_NOW - timedelta(days=14)).isoformat(),
        }],
    }

    assert proactive.is_handle_in_cooldown("simonwillison.net", state, _NOW) is False


def test_cooldown_false_for_different_handle():
    """A recent posted entry for handle A doesn't put handle B in cooldown."""
    state = {
        "pending": [], "rejected": [],
        "posted": [{
            "parent_author": "simonwillison.net",
            "posted_at": (_NOW - timedelta(days=1)).isoformat(),
        }],
    }

    assert proactive.is_handle_in_cooldown("xeiaso.net", state, _NOW) is False


def test_cooldown_ignores_pending_and_rejected_drafts():
    """Only ``posted`` counts — pending/rejected don't lock the handle."""
    recent = (_NOW - timedelta(hours=1)).isoformat()
    state = {
        "pending":  [{"parent_author": "simonwillison.net", "generated_at": recent}],
        "rejected": [{"parent_author": "simonwillison.net", "rejected_at": recent}],
        "posted":   [],
    }

    assert proactive.is_handle_in_cooldown("simonwillison.net", state, _NOW) is False


# ---------------------------------------------------------------------------
# filter_feed_item — one test per rule, fresh-engaging happy path first
# ---------------------------------------------------------------------------

def test_filter_accepts_fresh_engaging_text_post():
    item = _make_feed_item()
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is True
    assert reason == "ok"


def test_filter_rejects_repost():
    item = _make_feed_item(is_repost=True)
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "is_repost"


def test_filter_rejects_reply_post():
    item = _make_feed_item(is_reply=True)
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "is_reply"


def test_filter_rejects_quote_post_object_embed():
    """Quote-post embed exposed as an object with .record attribute → reject."""
    embed = SimpleNamespace(record=SimpleNamespace(uri="at://other"))
    item = _make_feed_item(embed=embed)
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "is_quote"


def test_filter_rejects_quote_post_dict_embed():
    """Defensive: same shape as a dict (SDK sometimes plain-dicts) → reject."""
    item = _make_feed_item(embed={"record": {"uri": "at://other"}})
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "is_quote"


def test_filter_rejects_empty_text():
    item = _make_feed_item(text="   ")
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "no_text"


def test_filter_rejects_post_older_than_12h():
    item = _make_feed_item(hours_ago=20)
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "too_old"


def test_filter_rejects_post_with_no_engagement():
    """Replies + reposts < MIN_PARENT_ENGAGEMENT (1) → dead post."""
    item = _make_feed_item(replies=0, reposts=0)
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "no_engagement"


def test_filter_rejects_reader_bait_parent():
    """Parent text matches BANNED_QUESTION_PATTERNS → don't chase reader-bait."""
    item = _make_feed_item(
        text="What do you think about the new model release? Curious to hear.",
    )
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "reader_bait"


def test_filter_accepts_likes_alone_do_not_satisfy_engagement_floor():
    """Plan defines engagement as replies + reposts — likes shouldn't help."""
    item = _make_feed_item(likes=100, reposts=0, replies=0)
    passes, reason = proactive.filter_feed_item(item, _NOW)
    assert passes is False
    assert reason == "no_engagement"


# ---------------------------------------------------------------------------
# pick_reply_candidate
# ---------------------------------------------------------------------------

def test_pick_returns_none_for_empty():
    assert proactive.pick_reply_candidate([]) is None


def test_pick_returns_highest_engagement():
    low = {"parent_author": "a", "engagement": 2, "indexed_at": "2026-05-21T17:00:00Z"}
    high = {"parent_author": "b", "engagement": 7, "indexed_at": "2026-05-21T17:00:00Z"}
    mid = {"parent_author": "c", "engagement": 5, "indexed_at": "2026-05-21T17:00:00Z"}

    pick = proactive.pick_reply_candidate([low, high, mid])

    assert pick is high


def test_pick_breaks_engagement_ties_by_newest_indexed():
    """Same engagement → most recently indexed wins (fresh posts → fresh replies)."""
    older = {"parent_author": "a", "engagement": 4, "indexed_at": "2026-05-21T10:00:00Z"}
    newer = {"parent_author": "b", "engagement": 4, "indexed_at": "2026-05-21T17:00:00Z"}

    pick = proactive.pick_reply_candidate([older, newer])

    assert pick is newer


# ---------------------------------------------------------------------------
# scan_handle (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_handle_returns_passing_candidates():
    """Feed with one passing item → one candidate; field projection correct."""
    item = _make_feed_item(text="A post worth replying to.", reposts=2, replies=3)
    client = _FakeBskyClient({"simonwillison.net": [item]})
    state = proactive._empty_state()

    candidates = await proactive.scan_handle(client, "simonwillison.net", state, _NOW)

    assert len(candidates) == 1
    c = candidates[0]
    assert c["parent_author"] == "simonwillison.net"
    assert c["platform"] == "bluesky"
    assert c["engagement"] == 5  # reposts + replies
    assert c["parent_text"] == "A post worth replying to."


@pytest.mark.asyncio
async def test_scan_handle_short_circuits_when_in_cooldown():
    """Handle in cooldown → no API call, empty candidate list."""
    state = {
        "pending": [], "rejected": [],
        "posted": [{
            "parent_author": "simonwillison.net",
            "posted_at": (_NOW - timedelta(days=2)).isoformat(),
        }],
    }
    client = _FakeBskyClient({"simonwillison.net": [_make_feed_item()]})

    candidates = await proactive.scan_handle(client, "simonwillison.net", state, _NOW)

    assert candidates == []
    assert client.calls == []  # short-circuited before fetch


@pytest.mark.asyncio
async def test_scan_handle_filters_out_rejections():
    """Mixed feed → only passing items become candidates."""
    items = [
        _make_feed_item(text="passes 1", reposts=2),
        _make_feed_item(is_repost=True),                 # rejected
        _make_feed_item(hours_ago=20),                   # rejected
        _make_feed_item(text="passes 2", reposts=3),
        _make_feed_item(replies=0, reposts=0),           # rejected
    ]
    client = _FakeBskyClient({"xeiaso.net": items})
    state = proactive._empty_state()

    candidates = await proactive.scan_handle(client, "xeiaso.net", state, _NOW)

    assert len(candidates) == 2
    assert {c["parent_text"] for c in candidates} == {"passes 1", "passes 2"}


@pytest.mark.asyncio
async def test_scan_handle_returns_empty_on_fetch_error():
    """Fetch raises → log + return empty, don't propagate."""
    client = _FakeBskyClient(raises_for={"simonwillison.net"})
    state = proactive._empty_state()

    candidates = await proactive.scan_handle(client, "simonwillison.net", state, _NOW)

    assert candidates == []


# ---------------------------------------------------------------------------
# scan_watchlist (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_watchlist_aggregates_per_handle():
    """Multi-handle scan → concatenated candidate list, expiry runs first."""
    feed_simonw = [_make_feed_item(text="from simon", reposts=2)]
    feed_xeiaso = [_make_feed_item(text="from xeiaso", reposts=4)]
    client = _FakeBskyClient({
        "simonwillison.net": feed_simonw,
        "xeiaso.net": feed_xeiaso,
    })
    # One expired pending draft to verify expiry runs as part of scan
    old_ts = (_NOW - timedelta(hours=30)).isoformat()
    state = {
        "pending": [{"id": "old", "parent_author": "x", "generated_at": old_ts}],
        "posted": [], "rejected": [],
    }

    candidates = await proactive.scan_watchlist(
        client, ["simonwillison.net", "xeiaso.net"], state, _NOW,
    )

    assert len(candidates) == 2
    assert {c["parent_text"] for c in candidates} == {"from simon", "from xeiaso"}
    # Expiry ran:
    assert state["pending"] == []
    assert len(state["rejected"]) == 1


# ---------------------------------------------------------------------------
# stage_draft_reply
# ---------------------------------------------------------------------------

def test_stage_appends_draft_with_uuid_and_expiry():
    candidate = {
        "platform": "bluesky",
        "parent_author": "xeiaso.net",
        "parent_post_uri": "at://did:plc:abc/app.bsky.feed.post/xyz",
        "parent_text": "OOMKilling at 1.5GB.",
        "engagement": 5,
        "indexed_at": _NOW.isoformat(),
    }
    state = proactive._empty_state()

    draft = proactive.stage_draft_reply(
        candidate, "cgroup v1 counts page cache against memory.", state, _NOW,
    )

    assert len(state["pending"]) == 1
    assert state["pending"][0] is draft
    assert len(draft["id"]) == 36  # UUID4 canonical length
    assert draft["draft_reply"].startswith("cgroup v1")
    assert draft["parent_author"] == "xeiaso.net"
    # Expiry = generated_at + DRAFT_EXPIRY_HOURS
    generated = datetime.fromisoformat(draft["generated_at"])
    expires = datetime.fromisoformat(draft["expires_at"])
    assert (expires - generated).total_seconds() == 24 * 3600


def test_stage_preserves_existing_pending_drafts():
    """Staging is additive — earlier pending entries don't get overwritten."""
    candidate = {
        "platform": "bluesky",
        "parent_author": "simonwillison.net",
        "parent_post_uri": "at://1",
        "parent_text": "post text",
        "engagement": 3,
        "indexed_at": _NOW.isoformat(),
    }
    existing = {"id": "earlier", "parent_author": "old", "generated_at": _NOW.isoformat()}
    state = {"pending": [existing], "posted": [], "rejected": []}

    proactive.stage_draft_reply(candidate, "draft body here.", state, _NOW)

    assert len(state["pending"]) == 2
    assert state["pending"][0] is existing
