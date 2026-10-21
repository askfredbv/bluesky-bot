"""The pre-broadcast reservation (review item S1, 2026-10-19).

`persistence_stage` recorded a story's dedup entry only after delivery, so a run
that posted and then died left the post live and unrecorded and the next run
could post it again. The reservation reverses the order — claim, broadcast,
settle — and these tests pin the paths that only fire when something goes wrong:
a claim that cannot be stored must abort the broadcast, a claim left behind must
suppress re-selection, and settlement must behave differently for a partial
delivery than for a run that reached nobody.
"""
from types import SimpleNamespace

import pytest

import main
from src import state_store
from src.config import MAX_BROADCAST_RESERVATIONS


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _local_only(monkeypatch, tmp_path):
    """No remote tier configured: the local file is the durable tier."""
    monkeypatch.delenv("STATE_STORE_URL", raising=False)
    monkeypatch.delenv("GIST_ID", raising=False)
    monkeypatch.setattr(state_store, "SEEN_FILE", tmp_path / "seen_articles.json")


def _events(monkeypatch):
    seen = []
    for level in ("info", "warn", "error"):
        monkeypatch.setattr(
            state_store.SafeLogger, level, lambda event, *a, **k: seen.append(event)
        )
    return seen


def _state(monkeypatch, initial):
    """Run reserve/settle against an in-memory state, returning it.

    Replaces the locked read-modify-write wholesale so these tests pin the
    mutator's decisions rather than the storage chain, which
    test_state_store_gaps.py already covers. The Gist is the configured tier and
    the one the write lands in, and the read-back is served from the same
    in-memory value, so a reservation here is accepted and nothing touches the
    network.

    ``box["trusted"]`` is what the verifying read reports, and
    ``box["gist_status"]`` is how the Gist tier answered it; set them to False /
    ``GIST_READ_UNREACHABLE`` to simulate a Gist GET that failed while the write
    itself had landed.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.delenv("STATE_STORE_URL", raising=False)
    box = {
        "value": dict(initial),
        "tier": "gist",
        "trusted": True,
        "gist_status": state_store.GIST_READ_OK,
    }

    def _fake(mutator, **kw):
        box["value"] = mutator(box["value"])
        return box["value"], box["tier"]

    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (box["value"], box["trusted"], box["gist_status"]),
    )
    return box


def _creds(mastodon_token="t"):
    return SimpleNamespace(
        gemini_api_key="g",
        anthropic_api_key=None,
        bluesky_username="u",
        bluesky_password="p",
        mastodon_access_token=mastodon_token,
        mastodon_api_base_url="https://masto",
    )


def _settings():
    return SimpleNamespace(
        platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0)
    )


def _curator_prep(seen_data=None):
    link = "https://news.example.com/article"
    return main.ContentPrepPayload(
        mode="curator",
        seen_data=seen_data if seen_data is not None else {"links": [], "recent_topics": []},
        news_items=[{"title": "Top", "link": link, "detected_topic": "General"}],
        link_meta={"title": "Top", "description": "", "image_data": None, "url": link},
        bsky_client="some-client",
        recent_posts=[],
        source_domain="news.example.com",
    )


def _stub_broadcast(monkeypatch, *, bsky_uris, mastodon_ids):
    """Wire broadcasting_stage up to platforms with fixed outcomes.

    Returns the attempt counter so a test can assert nothing went on the wire.
    """
    from src.metrics import BroadcastResult

    attempted = {"bsky": 0, "mastodon": 0}

    async def fake_generate(*args, **kwargs):
        return ["a post"], "Chosen Topic", "https://news.example.com/article"

    async def fake_bsky(client, content_list, link_meta, **kwargs):
        attempted["bsky"] += 1
        return BroadcastResult(client=client, sent_uris=list(bsky_uris))

    async def fake_mastodon(*args, **kwargs):
        attempted["mastodon"] += 1
        return BroadcastResult(client=None, sent_uris=list(mastodon_ids))

    async def no_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "generate_content", fake_generate)
    monkeypatch.setattr(main, "post_to_bluesky", fake_bsky)
    monkeypatch.setattr(main, "post_to_mastodon", fake_mastodon)
    monkeypatch.setattr(main, "apply_humanized_post_delay", no_delay)
    monkeypatch.setattr(main, "generate_mastodon_tags", lambda *a, **k: _none())
    monkeypatch.setattr(main.random, "choice", lambda seq: list(seq)[0])
    return attempted


async def _none():
    return []


# ---------------------------------------------------------------------------
# A claim that cannot be stored must abort the broadcast
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unstorable_reservation_aborts_the_broadcast(monkeypatch):
    """Posting with no record of having posted is the hole this closes, so a
    failed claim must skip the broadcast — not post and hope."""
    attempted = _stub_broadcast(monkeypatch, bsky_uris=["at://x"], mastodon_ids=["1"])
    settled = []
    monkeypatch.setattr(main, "reserve_broadcast", lambda link: False)
    monkeypatch.setattr(
        main, "settle_broadcast", lambda link, *, delivered, **kw: settled.append(delivered)
    )

    payload = await main.broadcasting_stage(_curator_prep(), _settings(), _creds())

    assert attempted == {"bsky": 0, "mastodon": 0}
    assert payload.content_list == []
    assert payload.bsky_sent_uris == []
    assert payload.mastodon_sent_ids == []
    # Nothing to settle: no claim was ever stored.
    assert settled == []


@pytest.mark.asyncio
async def test_an_unverifiable_read_still_broadcasts(monkeypatch):
    """The abort above is right for a REFUSED claim and wrong for an unreadable
    one, and this is the stage-level difference (assessment, 2026-10-19).

    Driving the real `reserve_broadcast` with a verifying read that failed: the
    write landed in the Gist, the read-back cannot see it, and the run must post
    anyway. Refusing here cost the run both halves — nothing broadcast, and the
    claim still in the Gist to suppress the story on the next healthy run.
    """
    attempted = _stub_broadcast(monkeypatch, bsky_uris=["at://x"], mastodon_ids=["1"])
    settled = []
    monkeypatch.setenv("GIST_ID", "abc123")
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "gist")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            False,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )
    monkeypatch.setattr(main, "reserve_broadcast", state_store.reserve_broadcast)
    monkeypatch.setattr(
        main, "settle_broadcast", lambda link, *, delivered, **kw: settled.append(delivered)
    )

    payload = await main.broadcasting_stage(_curator_prep(), _settings(), _creds())

    assert attempted == {"bsky": 1, "mastodon": 1}
    assert payload.bsky_sent_uris == ["at://x"]
    assert payload.mastodon_sent_ids == ["1"]
    # And the claim is settled, which the abort path never reached.
    assert settled == [True]


@pytest.mark.asyncio
async def test_abort_leaves_post_dependent_state_untouched(monkeypatch):
    """The abort must look like any other undelivered run to persistence."""
    _stub_broadcast(monkeypatch, bsky_uris=["at://x"], mastodon_ids=["1"])
    monkeypatch.setattr(main, "reserve_broadcast", lambda link: False)
    monkeypatch.setattr(main, "settle_broadcast", lambda link, *, delivered, **kw: None)

    payload = await main.broadcasting_stage(_curator_prep(), _settings(), _creds())
    automation = main.build_automation_payload(payload)

    assert automation.delivered is False
    writes = []
    monkeypatch.setattr(main, "update_seen_articles", lambda mutator: writes.append(mutator))
    await main.persistence_stage(automation)
    assert writes == []


@pytest.mark.asyncio
async def test_a_reservation_is_taken_before_anything_is_posted(monkeypatch):
    """Order is the whole point: the claim must precede the first send."""
    order = []
    from src.metrics import BroadcastResult

    async def fake_generate(*args, **kwargs):
        return ["a post"], "Chosen Topic", "https://news.example.com/article"

    async def fake_bsky(client, content_list, link_meta, **kwargs):
        order.append("bsky")
        return BroadcastResult(client=client, sent_uris=["at://x"])

    async def fake_mastodon(*args, **kwargs):
        order.append("mastodon")
        return BroadcastResult(client=None, sent_uris=["1"])

    async def no_delay(*args, **kwargs):
        return None

    def fake_reserve(link):
        order.append("reserve")
        return True

    monkeypatch.setattr(main, "generate_content", fake_generate)
    monkeypatch.setattr(main, "post_to_bluesky", fake_bsky)
    monkeypatch.setattr(main, "post_to_mastodon", fake_mastodon)
    monkeypatch.setattr(main, "apply_humanized_post_delay", no_delay)
    monkeypatch.setattr(main, "generate_mastodon_tags", lambda *a, **k: _none())
    monkeypatch.setattr(main.random, "choice", lambda seq: list(seq)[0])
    monkeypatch.setattr(main, "reserve_broadcast", fake_reserve)
    monkeypatch.setattr(main, "settle_broadcast", lambda link, *, delivered, **kw: order.append("settle"))

    await main.broadcasting_stage(_curator_prep(), _settings(), _creds())

    assert order[0] == "reserve"
    assert order[-1] == "settle"


@pytest.mark.asyncio
async def test_mentor_mode_takes_no_reservation(monkeypatch):
    """Mentor and Strategist have no story URL to claim — their dedup is the
    free-form topic memory, not a per-story entry."""
    _stub_broadcast(monkeypatch, bsky_uris=["at://x"], mastodon_ids=["1"])
    reserved = []
    monkeypatch.setattr(main, "reserve_broadcast", lambda link: reserved.append(link) or True)
    monkeypatch.setattr(main, "settle_broadcast", lambda link, *, delivered, **kw: None)

    prep = main.ContentPrepPayload(
        mode="mentor",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        link_meta=None,
        bsky_client="some-client",
        recent_posts=[],
    )
    payload = await main.broadcasting_stage(prep, _settings(), _creds())

    assert reserved == []
    assert payload.bsky_sent_uris == ["at://x"]


# ---------------------------------------------------------------------------
# A claim left behind must suppress re-selection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_live_reservation_is_handed_to_selection_as_seen(monkeypatch):
    """The next run must treat a claim as already-seen, or the claim is inert."""
    passed = {}

    async def fake_fetch_news(seen_links, recent_topics, *args, **kwargs):
        passed["links"] = list(seen_links)
        return []

    monkeypatch.setattr(main, "fetch_news", fake_fetch_news)
    monkeypatch.setattr(
        main,
        "load_seen_articles",
        lambda: {
            "links": ["https://already.example.com/a"],
            "recent_topics": [],
            "pending_broadcast": [
                {"link": "https://claimed.example.com/b", "stage": "pending"}
            ],
        },
    )

    class _NoClient:
        def __init__(self, *a, **k):
            raise RuntimeError("no bluesky in this test")

    monkeypatch.setattr("atproto.AsyncClient", _NoClient)

    await main.content_prep_stage(
        main.ModeSelectionPayload(mode="curator", current_hour_utc=9), _creds()
    )

    assert passed["links"] == [
        "https://already.example.com/a",
        "https://claimed.example.com/b",
    ]


def test_reserved_links_reads_a_state_file_that_predates_the_field():
    """Self-healing, no migration step: old state simply has no reservations."""
    assert state_store.reserved_links({"links": [], "recent_topics": []}) == []
    assert state_store.reserved_links({"pending_broadcast": None}) == []


def test_reserved_links_skips_malformed_entries():
    """A junk entry must not crash candidate selection."""
    links = state_store.reserved_links({
        "pending_broadcast": [
            "not-a-dict",
            {},
            {"link": ""},
            {"link": None},
            {"link": "https://good.example.com/a", "stage": "pending"},
        ]
    })
    assert links == ["https://good.example.com/a"]


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_delivery_settles_as_delivered(monkeypatch):
    """One platform landing — or one part of a thread — means the post is
    public, so the claim becomes an ordinary seen-link."""
    _stub_broadcast(monkeypatch, bsky_uris=["at://only-the-first"], mastodon_ids=[])
    settled = []
    monkeypatch.setattr(main, "reserve_broadcast", lambda link: True)
    monkeypatch.setattr(
        main,
        "settle_broadcast",
        lambda link, *, delivered, **kw: settled.append((link, delivered)),
    )

    await main.broadcasting_stage(_curator_prep(), _settings(), _creds())

    assert settled == [("https://news.example.com/article", True)]


@pytest.mark.asyncio
async def test_total_failure_settles_as_not_delivered(monkeypatch):
    """Neither platform answered: settle as undelivered so the claim is kept."""
    _stub_broadcast(monkeypatch, bsky_uris=[], mastodon_ids=[])
    settled = []
    monkeypatch.setattr(main, "reserve_broadcast", lambda link: True)
    monkeypatch.setattr(
        main,
        "settle_broadcast",
        lambda link, *, delivered, **kw: settled.append((link, delivered)),
    )

    payload = await main.broadcasting_stage(_curator_prep(), _settings(), _creds())

    assert settled == [("https://news.example.com/article", False)]
    assert main.build_automation_payload(payload).delivered is False


def test_settlement_after_delivery_promotes_the_claim_to_a_seen_link(monkeypatch):
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [
            {"link": "https://a.example.com/x", "stage": "pending", "reserved_at": "t0"}
        ],
    })

    state_store.settle_broadcast("https://a.example.com/x", delivered=True)

    assert box["value"]["links"] == ["https://a.example.com/x"]
    assert box["value"]["pending_broadcast"] == []


def test_settlement_after_delivery_does_not_double_count_the_window(monkeypatch):
    """persistence_stage writes the same link moments later; one slot, not two."""
    box = _state(monkeypatch, {
        "links": ["https://a.example.com/x"],
        "recent_topics": [],
        "pending_broadcast": [{"link": "https://a.example.com/x", "stage": "pending"}],
    })

    state_store.settle_broadcast("https://a.example.com/x", delivered=True)

    assert box["value"]["links"] == ["https://a.example.com/x"]


def test_settlement_after_total_failure_keeps_the_claim_as_uncertain(monkeypatch):
    """A send that landed and lost its response looks identical to one that never
    landed. Only the suppressing reading is safe, so the claim is kept."""
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [
            {"link": "https://a.example.com/x", "stage": "pending", "reserved_at": "t0"}
        ],
    })

    state_store.settle_broadcast("https://a.example.com/x", delivered=False)

    assert box["value"]["links"] == []
    entry = box["value"]["pending_broadcast"][0]
    assert entry["link"] == "https://a.example.com/x"
    assert entry["stage"] == state_store.RESERVATION_STAGE_UNCERTAIN
    assert entry["reserved_at"] == "t0"  # when the claim was taken, preserved
    assert entry["settled_at"]
    # Still suppresses, which is the whole reason for keeping it.
    assert state_store.reserved_links(box["value"]) == ["https://a.example.com/x"]


def test_settlement_releases_the_claim_when_nothing_tried(monkeypatch):
    """Nothing attempted means nothing can have been posted, so there is no
    ambiguity to protect against and the story goes back in the pool
    (Codex, 2026-10-19)."""
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [{"link": "https://a.example.com/x", "stage": "pending"}],
    })

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert box["value"]["pending_broadcast"] == []
    assert box["value"]["links"] == []  # not posted, so not a seen-link either
    assert state_store.reserved_links(box["value"]) == []


def test_a_shadowed_release_is_reported_not_claimed_as_settled(monkeypatch):
    """The release is the one settlement outcome that does NOT fail safe
    (Codex, 2026-10-19): a write accepted by a tier reads do not select leaves
    the story suppressed while the log says it was freed."""
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({"links": []}), "store")
    )
    # What reads actually come back with: the stale Gist, claim still in it.
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_OK,
        ),
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert "broadcast_release_not_readable" in events
    assert "broadcast_settled" not in events


def test_a_shadowed_keep_or_promote_is_not_flagged(monkeypatch):
    """Those two DO fail safe — the surviving pending claim suppresses a story
    that was posted, or that a platform tried to post. No false alarm."""
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({"links": []}), "store")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_OK,
        ),
    )

    state_store.settle_broadcast("https://a.example.com/x", delivered=True)
    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=True
    )

    assert "broadcast_release_not_readable" not in events
    assert events.count("broadcast_settled") == 2


def test_an_unverifiable_release_is_reported_as_unverified(monkeypatch):
    """The log-only half of the same bug, in the opposite direction
    (assessment, 2026-10-19).

    The release write was accepted. The verifying read then fails, and the
    non-strict wrapper's empty default made the surviving claim look gone — so
    the check that exists to catch a shadowed release logged
    `broadcast_settled` instead, reporting a release it could not confirm.
    """
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({"links": []}), "store")
    )
    # The read that would confirm the release failed: empty, and untrusted.
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            False,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert "broadcast_release_unverified" in events
    # Neither of the two things it is NOT: a confirmed settlement, or a
    # confirmed shadowed release.
    assert "broadcast_settled" not in events
    assert "broadcast_release_not_readable" not in events


def test_a_release_confirmed_only_by_the_store_is_not_reported_as_settled(monkeypatch):
    """Site 2's version of the same aggregate-trust hole (Codex, 2026-10-19).

    The release write went to the store because the Gist PATCH failed, and the
    verifying read fell through to the store because the Gist GET failed. The
    claim is gone from the store, so the old check called it settled — while the
    Gist still carries the claim and will answer first once it recovers, leaving
    the story suppressed.
    """
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({"links": []}), "store")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            True,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert "broadcast_release_unverified" in events
    assert "broadcast_settled" not in events


def test_a_surviving_claim_under_a_corrupt_gist_is_an_error_not_a_warning(monkeypatch):
    """Corruption makes the fallback read authoritative, not useless
    (Codex, 2026-10-19).

    Reads fall straight PAST a corrupt Gist, so what the store returned is
    literally what a reader gets — and the claim is still in it. That is an
    observed failed release: the story stays suppressed although nothing was
    posted. Reporting it as "could not verify" hid a critical failure behind a
    WARN, where the Actions UI surfaces ERROR.
    """
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store,
        "update_seen_articles_checked",
        lambda m, **kw: (m({"links": []}), "store"),
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_CORRUPT,
        ),
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert "broadcast_release_not_readable" in events
    assert "broadcast_release_unverified" not in events
    assert "broadcast_settled" not in events


def test_a_surviving_claim_under_an_unreachable_gist_stays_unverified(monkeypatch):
    """The asymmetry that justifies keeping the two statuses apart: an
    unreachable Gist answers first the moment it is back, so what the store
    returned says nothing about what a reader will get — present or absent."""
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store,
        "update_seen_articles_checked",
        lambda m, **kw: (m({"links": []}), "store"),
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert "broadcast_release_unverified" in events
    assert "broadcast_release_not_readable" not in events


def test_a_corrupt_gist_with_the_claim_gone_is_still_only_unverified(monkeypatch):
    """Corruption is authoritative about what reads return NOW, not about what
    the Gist itself holds — so an absent claim there is not a confirmed
    release."""
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store,
        "update_seen_articles_checked",
        lambda m, **kw: (m({"links": []}), "store"),
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            True,
            state_store.GIST_READ_CORRUPT,
        ),
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert "broadcast_release_unverified" in events
    assert "broadcast_settled" not in events


def test_a_confirmed_release_is_reported_as_settled(monkeypatch):
    """The positive side of the pair: a read the code CAN trust, showing the
    claim gone, is the one case that may say `broadcast_settled`."""
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({"links": []}), "gist")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            True,
            state_store.GIST_READ_OK,
        ),
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert "broadcast_settled" in events
    assert "broadcast_release_unverified" not in events
    assert "broadcast_release_not_readable" not in events


def test_an_unverifiable_read_does_not_touch_the_other_two_outcomes(monkeypatch):
    """Promote and keep are not verified at all, trusted read or not — they
    fail safe, and flagging them would be the over-correction the shadowed-keep
    test already pins."""
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({"links": []}), "store")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            False,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    state_store.settle_broadcast("https://a.example.com/x", delivered=True)
    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=True
    )

    assert "broadcast_release_unverified" not in events
    assert events.count("broadcast_settled") == 2


def test_settlement_defaults_to_the_suppressing_reading(monkeypatch):
    """A caller that does not pass `attempted` must get the safe behaviour."""
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [{"link": "https://a.example.com/x", "stage": "pending"}],
    })

    state_store.settle_broadcast("https://a.example.com/x", delivered=False)

    assert state_store.reserved_links(box["value"]) == ["https://a.example.com/x"]


@pytest.mark.asyncio
async def test_no_usable_platform_releases_the_claim(monkeypatch):
    """Bluesky session failed and Mastodon switched off: the only scheduled
    broadcaster returns without posting, so the claim must not be held."""
    monkeypatch.setattr(main, "reserve_broadcast", lambda link: True)
    settled = []
    monkeypatch.setattr(
        main,
        "settle_broadcast",
        lambda link, *, delivered, attempted: settled.append((delivered, attempted)),
    )
    _stub_broadcast(monkeypatch, bsky_uris=[], mastodon_ids=[])

    # No Mastodon token, and no Bluesky client to schedule a task with.
    async def fake_mastodon(access_token, *args, **kwargs):
        from src.metrics import BroadcastResult
        return BroadcastResult(client=None, sent_uris=[], attempted=False)

    monkeypatch.setattr(main, "post_to_mastodon", fake_mastodon)

    class _NoClient:
        def __init__(self, *a, **k):
            raise RuntimeError("no session")

    monkeypatch.setattr("atproto.AsyncClient", _NoClient)
    prep = _curator_prep()
    prep = main.ContentPrepPayload(
        mode=prep.mode,
        seen_data=prep.seen_data,
        news_items=prep.news_items,
        link_meta=prep.link_meta,
        bsky_client=None,  # preflight failed
        recent_posts=[],
        source_domain=prep.source_domain,
    )

    await main.broadcasting_stage(prep, _settings(), _creds(mastodon_token=""))

    assert settled == [(False, False)]


@pytest.mark.asyncio
async def test_a_platform_that_tried_and_failed_keeps_the_claim(monkeypatch):
    """The ambiguous case must still settle as attempted."""
    monkeypatch.setattr(main, "reserve_broadcast", lambda link: True)
    settled = []
    monkeypatch.setattr(
        main,
        "settle_broadcast",
        lambda link, *, delivered, attempted: settled.append((delivered, attempted)),
    )
    _stub_broadcast(monkeypatch, bsky_uris=[], mastodon_ids=[])

    await main.broadcasting_stage(_curator_prep(), _settings(), _creds())

    assert settled == [(False, True)]


def test_platform_attempted_reads_the_result_and_the_two_cases_it_cannot_carry():
    """Not scheduled = did not try. A raised task has no result and counts as
    tried, because it may have raised after reaching the wire."""
    from src.metrics import BroadcastResult

    assert main._platform_attempted(False, None) is False
    assert main._platform_attempted(False, BroadcastResult(attempted=True)) is False
    assert main._platform_attempted(True, None) is True  # task raised: ambiguous
    assert main._platform_attempted(True, BroadcastResult(attempted=False)) is False
    assert main._platform_attempted(True, BroadcastResult(attempted=True)) is True


def test_broadcasters_mark_the_pre_wire_bailouts_as_not_attempted(monkeypatch):
    """The three paths that return an empty id list WITHOUT sending must say so,
    or the reservation cannot tell them from a failed send."""
    import asyncio

    from src import broadcasters

    # Mastodon with no token: switched off, never tried.
    result = asyncio.run(broadcasters.post_to_mastodon("", "https://masto", ["a post"]))
    assert result.sent_uris == []
    assert result.attempted is False

    # Length invariant refusing the content, both platforms.
    monkeypatch.setattr(broadcasters, "_enforce_post_length_invariant", lambda *a: False)
    bsky = asyncio.run(broadcasters.post_to_bluesky("client", ["a post"], None))
    assert bsky.attempted is False
    masto = asyncio.run(
        broadcasters.post_to_mastodon("token", "https://masto", ["a post"])
    )
    assert masto.attempted is False


def test_a_bluesky_raise_before_the_first_send_is_not_attempted(monkeypatch):
    """Everything above the first send — blob upload, embed and facet building —
    runs inside the same try, so a raise there returned an empty id list that
    looked like a failed send (Codex, 2026-10-19)."""
    import asyncio

    from src import broadcasters

    def _boom(*args, **kwargs):
        raise ValueError("malformed OpenGraph metadata")

    # build_facets runs for every post, before the send call.
    monkeypatch.setattr(broadcasters, "build_facets", _boom)

    result = asyncio.run(broadcasters.post_to_bluesky("client", ["a post"], None))

    assert result.sent_uris == []
    assert result.error is not None
    assert result.attempted is False  # definitively pre-wire


def test_a_bluesky_raise_inside_the_send_counts_as_attempted(monkeypatch):
    """The ambiguous case: it may have raised after reaching the wire, so the
    claim must still be held."""
    import asyncio

    from src import broadcasters

    class _FailingClient:
        async def send_post(self, **kwargs):
            raise RuntimeError("connection reset mid-send")

    async def _reraise(attempts, exc, **kwargs):
        raise exc  # no backoff sleeps in the suite

    monkeypatch.setattr(broadcasters, "build_facets", lambda text: None)
    monkeypatch.setattr(broadcasters, "sleep_for_transient", _reraise)

    result = asyncio.run(
        broadcasters.post_to_bluesky(_FailingClient(), ["a post"], None)
    )

    assert result.sent_uris == []
    assert result.attempted is True


def test_a_delivered_broadcast_reports_attempted(monkeypatch):
    """The default must stay True for every normal return path."""
    from src.metrics import BroadcastResult

    assert BroadcastResult().attempted is True
    assert BroadcastResult(sent_uris=["at://x"]).attempted is True


def test_settlement_after_total_failure_records_a_claim_that_went_missing(monkeypatch):
    """If the claim is gone from state (an untrusted read skipped its write), an
    undelivered settlement still leaves the story marked uncertain."""
    box = _state(monkeypatch, {"links": [], "recent_topics": [], "pending_broadcast": []})

    state_store.settle_broadcast("https://a.example.com/x", delivered=False)

    assert state_store.reserved_links(box["value"]) == ["https://a.example.com/x"]


def test_settlement_leaves_other_claims_alone(monkeypatch):
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [
            {"link": "https://other.example.com/y", "stage": "uncertain"},
            {"link": "https://a.example.com/x", "stage": "pending"},
        ],
    })

    state_store.settle_broadcast("https://a.example.com/x", delivered=True)

    assert state_store.reserved_links(box["value"]) == ["https://other.example.com/y"]


def test_failed_settlement_logs_an_error_and_leaves_the_claim_pending(monkeypatch):
    """Best-effort, but loud: the state file now carries a claim nobody will
    settle, and a pending claim suppresses just as firmly as an uncertain one."""
    events = _events(monkeypatch)

    def _fake(mutator, **kw):
        return {}, None

    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)

    state_store.settle_broadcast("https://a.example.com/x", delivered=True)

    assert "broadcast_settlement_failed" in events
    assert "broadcast_settled" not in events


# ---------------------------------------------------------------------------
# Settlement over a Gist that went corrupt mid-run (#201)
# ---------------------------------------------------------------------------

def _live_write(monkeypatch, *, state, gist_status):
    """The REAL locked write, with the read chain's answer fixed.

    The `_state` harness above replaces `update_seen_articles_checked` wholesale,
    which is what makes it useful for pinning the mutator's decisions and
    useless here: the guard under test lives inside the function it replaces. So
    these tests stub only the read that function makes, and capture what
    `save_seen_articles` is handed.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())
    saved = []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d) or "gist")
    monkeypatch.setattr(
        state_store, "load_seen_articles_sourced", lambda: (state, True, gist_status)
    )
    return saved


def test_an_uncertain_settlement_is_written_over_a_gist_that_went_corrupt(monkeypatch):
    """The repost window #201 describes, end to end.

    `reserve_broadcast` refuses outright on corruption, so the Gist was
    parseable when the claim was verified — and `save_seen_articles` stops at
    the first tier that accepts, so a Gist-tier claim exists nowhere else. The
    Gist then goes corrupt, and every read falls straight past it to a store
    snapshot that never held the claim. If `require_gist_read` withholds the
    settlement as well, NOTHING suppresses the story: the next run can select
    and post it although a platform may already have taken it.
    """
    saved = _live_write(
        monkeypatch,
        # What a reader now gets: the older store snapshot, no claim in it.
        state={"links": [], "recent_topics": [], "pending_broadcast": []},
        gist_status=state_store.GIST_READ_CORRUPT,
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=True
    )

    assert len(saved) == 1
    assert state_store.reserved_links(saved[-1]) == ["https://a.example.com/x"]


def test_a_delivered_settlement_is_also_written_over_a_corrupt_gist(monkeypatch):
    """The other suppressing outcome, same reasoning: the `links` entry is what
    keeps the next run off a story that is already live."""
    saved = _live_write(
        monkeypatch,
        state={"links": [], "recent_topics": [], "pending_broadcast": []},
        gist_status=state_store.GIST_READ_CORRUPT,
    )

    state_store.settle_broadcast("https://a.example.com/x", delivered=True)

    assert saved[-1]["links"] == ["https://a.example.com/x"]


def test_an_uncertain_settlement_still_refuses_an_unreachable_gist(monkeypatch):
    """The asymmetry, at the settlement end.

    An unreachable Gist probably still holds the pending claim, which suppresses
    the story the moment it answers again — so the fail-safe reading holds, and
    writing a store-derived snapshot over it would erase the claim doing the
    work. Missing the write is the cheaper failure here; under corruption it is
    not.
    """
    saved = _live_write(
        monkeypatch,
        state={"links": [], "recent_topics": [], "pending_broadcast": []},
        gist_status=state_store.GIST_READ_UNREACHABLE,
    )
    events = _events(monkeypatch)

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=True
    )

    assert saved == []
    assert "broadcast_settlement_failed" in events


def test_a_release_does_not_overwrite_a_corrupt_gist(monkeypatch):
    """Deliberately NOT widened. The release wants the claim gone, and corrupt
    content already makes it unreadable, so an overwrite buys nothing — and it
    would destroy content a human might still recover by hand."""
    saved = _live_write(
        monkeypatch,
        state={"links": [], "recent_topics": [], "pending_broadcast": []},
        gist_status=state_store.GIST_READ_CORRUPT,
    )

    state_store.settle_broadcast(
        "https://a.example.com/x", delivered=False, attempted=False
    )

    assert saved == []


# ---------------------------------------------------------------------------
# What counts as a stored claim
# ---------------------------------------------------------------------------

def test_reservation_fails_when_no_tier_stored_it(monkeypatch):
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store, "update_seen_articles_checked", lambda m, **kw: ({}, None))

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_failed" in events


def test_reservation_fails_when_only_the_local_file_took_it_on_a_runner(monkeypatch):
    """A remote tier is configured and refused: the local file dies with the
    GitHub Actions runner, so calling that stored would be a false positive."""
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({}), "local")
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_not_durable" in events


def test_reservation_fails_when_the_claim_does_not_read_back(monkeypatch):
    """A Gist write-only outage with a healthy fallback store (Codex, 2026-10-19).

    `load_seen_articles_strict` returns a parseable Gist payload unconditionally,
    so a claim written only to STATE_STORE_URL is shadowed: the next run reloads
    the unchanged Gist, misses the claim, and reposts the story. The write
    succeeded and the reservation must still be refused — which the read-back
    detects without needing to know which tier is authoritative.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({}), "store")
    )
    # What a later read comes back with: the stale Gist, no claim in it.
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": [], "recent_topics": []}, True, state_store.GIST_READ_OK),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_not_readable" in events


def test_reservation_succeeds_when_the_read_falls_through_to_the_store(monkeypatch):
    """The configuration the tier-inference guard got wrong (Codex, 2026-10-19).

    A reachable Gist that holds no seen_articles.json makes the strict read fall
    through to STATE_STORE_URL, so a claim written there IS observable. Inferring
    "the Gist is authoritative" from GIST_ID alone rejected it and aborted every
    Curator broadcast in that configuration; the read-back accepts it.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({}), "store")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_OK,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is True


def test_a_refused_reservation_releases_the_claim_it_wrote(monkeypatch):
    """Write lands, and a read the code CAN trust does not show the claim
    (Codex, 2026-10-19).

    The caller aborts the broadcast and never reaches settlement, so a claim left
    behind here would suppress — forever, since claims are not pruned by age — a
    story that definitively never posted. The trust flag is what separates this
    from the unverifiable read below: a trusted read saying "absent" is evidence,
    and it still refuses.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    state = {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [],
    }

    def _fake(mutator, **kw):
        return mutator(state), "gist"

    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)
    # The verification read fails: it sees state without the claim.
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": [], "recent_topics": []}, True, state_store.GIST_READ_OK),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_not_readable" in events
    assert "broadcast_claim_released" in events
    # The story is back in the pool, not silently lost.
    assert state_store.reserved_links(state) == []


def test_an_unverifiable_read_proceeds_with_the_broadcast(monkeypatch):
    """The hole the strict read closes (assessment, 2026-10-19).

    The claim was written and accepted by the Gist. The verifying read then
    fails, and the non-strict wrapper handed that back as the empty default —
    indistinguishable from "the claim is not there". The old code refused,
    released (a release whose own strict read was untrusted too, so it wrote
    nothing), and aborted the broadcast: the claim stayed in the Gist, nothing
    was posted, and the next healthy run skipped the story. One transient GET
    did all of it.

    An unreadable state is not evidence of anything. The durability guards above
    have already established the write reached a tier reads consult, so the
    claim is almost certainly stored — proceed, and say the proof is missing.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}

    def _fake(mutator, **kw):
        return mutator(state), "gist"

    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)
    # The Gist GET failed: the empty default, flagged as NOT trustworthy.
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            False,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is True
    assert "broadcast_reservation_unverified" in events
    # Not the trusted-absent path: nothing is refused and nothing is released.
    assert "broadcast_reservation_not_readable" not in events
    assert "broadcast_claim_released" not in events
    assert "broadcast_claim_release_failed" not in events
    # The claim the run is broadcasting under is still standing.
    assert state_store.reserved_links(state) == ["https://a.example.com/x"]


def test_an_unverifiable_claim_on_a_shadowable_tier_is_refused(monkeypatch):
    """Proceeding unverified is safe for the Gist and ONLY for the Gist
    (Codex, 2026-10-19).

    An untrusted read means GIST_ID is set and the Gist is unreadable — that is
    the only route to `trusted=False`. If the claim is sitting in the fallback
    store, the Gist PATCH must have failed, so the moment the Gist recovers its
    older seen_articles.json answers reads and the store is never consulted. The
    claim is invisible, the next run reselects the story, and it goes out twice.
    That is the window the reservation exists to close, so this one is refused
    even though refusing costs the run.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "store")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            False,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_shadowable" in events
    # Not the Gist path: nothing is broadcast on an unverified fallback write.
    assert "broadcast_reservation_unverified" not in events


def test_a_store_claim_read_back_from_the_store_is_refused_while_the_gist_is_silent(monkeypatch):
    """The wide version of the shadow hole, and the one that actually fires
    (Codex, 2026-10-19).

    `load_seen_articles_strict` reports `trusted=True` the moment the STORE
    supplies valid state, even when the Gist GET failed on the way past. So the
    realistic outage — Gist down for both PATCH and GET, store healthy — writes
    the claim to the store, reads it straight back out of the store, and passes
    verification. Then the Gist recovers, its older file answers first, the
    claim is gone, and the story is reposted.

    The aggregate trust flag cannot see this; `gist_settled` can.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "store")
    )
    # Trusted, because the store answered with real state — and the claim IS in
    # it. The Gist, which reads ask first, did not answer at all.
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_shadowable" in events
    # The claim read back fine, so this is NOT the absent-claim path.
    assert "broadcast_reservation_not_readable" not in events


def test_a_settled_gist_with_no_file_still_verifies_a_store_claim(monkeypatch):
    """The configuration this must not break, pinned against the fix above.

    A Gist that is reachable and simply holds no seen_articles.json makes reads
    fall through to the store legitimately — the next run falls through exactly
    the same way, so a store claim IS durable. `gist_settled` is True here even
    though the Gist supplied nothing, which is the whole distinction.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m({}), "store")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_OK,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is True
    assert "broadcast_reservation_shadowable" not in events


def test_a_shadowable_refusal_whose_cleanup_also_fails_is_loud(monkeypatch):
    """The combined failure: both reads down AND the store took the write
    (Codex, 2026-10-19).

    `_release_claim` goes through `update_seen_articles_checked`, whose own
    strict read fails for the same reason the verification did, so the cleanup
    write is skipped and the claim is orphaned in the store. Not repaired here —
    writing a remembered pre-claim state back to one tier without a fresh read
    is exactly the stale-snapshot overwrite that `update_seen_articles_checked`
    exists to prevent, and it would clobber whatever else that tier holds. The
    requirement is that the run refuses and says so at ERROR, both of which are
    pinned here, rather than that the state comes out clean.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    writes = {"n": 0}

    def _fake(mutator, **kw):
        writes["n"] += 1
        if writes["n"] == 1:
            return mutator(state), "store"
        # The release's own read is untrusted, so its write is skipped.
        return state, None

    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            False,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_shadowable" in events
    assert "broadcast_claim_release_failed" in events
    assert "broadcast_claim_released" not in events
    # Orphaned, and known to be: the claim is still there and the log says so.
    assert state_store.reserved_links(state) == ["https://a.example.com/x"]


def test_a_corrupt_gist_refuses_even_a_gist_tier_claim(monkeypatch):
    """Corruption is evidence, not a failed check (Codex, 2026-10-19).

    The PATCH succeeded, so the tier gate would wave this through as "the Gist
    took the write, proceed unverified". But the GET came back: the Gist holds
    content no read can parse, so every read falls PAST it to older store or
    local state with no claim in it, and the story is selectable again however
    well the write went. `_save_gist_state` writes valid JSON, so seeing
    corruption after our own successful PATCH means what is there is not what
    we wrote. Refuse.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "gist")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            True,
            state_store.GIST_READ_CORRUPT,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_gist_unparseable" in events
    # Specifically NOT the transient path, which would have broadcast.
    assert "broadcast_reservation_unverified" not in events


def test_an_unreachable_gist_is_not_treated_as_corrupt(monkeypatch):
    """The other side of that branch: an unanswered request is the absence of
    evidence, and a Gist-tier write still proceeds on it."""
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "gist")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            False,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is True
    assert "broadcast_reservation_unverified" in events
    assert "broadcast_reservation_gist_unparseable" not in events


def test_a_corrupt_gist_also_refuses_a_store_tier_claim(monkeypatch):
    """Corruption outranks the tier question — both roads lead to a refusal,
    but the log must say which one it was."""
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "store")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            True,
            state_store.GIST_READ_CORRUPT,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_gist_unparseable" in events
    assert "broadcast_reservation_shadowable" not in events


def test_an_unverifiable_claim_on_the_gist_is_not_treated_as_shadowable(monkeypatch):
    """The other side of the same branch, so it cannot drift into refusing
    everything: the Gist took the write, and nothing precedes it in either the
    read or the write chain."""
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setenv("STATE_STORE_URL", "https://state.example/bot")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "gist")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {"links": [], "recent_topics": [], "pioneer_recent": []},
            False,
            state_store.GIST_READ_UNREACHABLE,
        ),
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is True
    assert "broadcast_reservation_shadowable" not in events


def test_a_non_durable_reservation_also_releases_its_claim(monkeypatch):
    """Same cleanup on the ephemeral-runner rejection."""
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "local")
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_claim_released" in events
    assert state_store.reserved_links(state) == []


def test_a_failed_release_is_loud(monkeypatch):
    """If the cleanup write is refused too, the story IS stuck — say so."""
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store, "update_seen_articles_checked", lambda m, **kw: ({}, None))

    state_store._release_claim("https://a.example.com/x", reason="not_readable")

    assert "broadcast_claim_release_failed" in events
    assert "broadcast_claim_released" not in events


def test_release_leaves_other_claims_alone(monkeypatch):
    state = {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [
            {"link": "https://keep.example.com/a", "stage": "uncertain"},
            {"link": "https://a.example.com/x", "stage": "pending"},
        ],
    }
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m, **kw: (m(state), "gist")
    )

    state_store._release_claim("https://a.example.com/x", reason="not_readable")

    assert state_store.reserved_links(state) == ["https://keep.example.com/a"]


def test_reservation_reads_back_through_the_real_chain(monkeypatch, tmp_path):
    """End to end on the local tier: write, then prove the claim is readable by
    the same call the next run makes."""
    _local_only(monkeypatch, tmp_path)

    assert state_store.reserve_broadcast("https://a.example.com/x") is True
    assert "https://a.example.com/x" in state_store.reserved_links(
        state_store.load_seen_articles()
    )


def test_reservation_succeeds_on_the_local_tier_in_local_development(monkeypatch, tmp_path):
    """With no remote tier configured the local file IS the durable tier, so a
    developer run is not permanently unable to broadcast."""
    _local_only(monkeypatch, tmp_path)

    assert state_store.reserve_broadcast("https://a.example.com/x") is True
    assert state_store.reserved_links(state_store.load_seen_articles()) == [
        "https://a.example.com/x"
    ]


def test_reservation_canonicalises_the_link(monkeypatch):
    """The claim and the eventual seen-link must be the same string, or the
    suppression misses."""
    box = _state(monkeypatch, {"links": [], "recent_topics": []})

    state_store.reserve_broadcast("https://a.example.com/x?utm_source=rss#frag")

    from src.net_safety import canonical_url
    assert state_store.reserved_links(box["value"]) == [
        canonical_url("https://a.example.com/x?utm_source=rss#frag")
    ]


def test_an_existing_claim_is_a_failed_acquisition_not_a_retry(monkeypatch):
    """The sharpest version of the bug this PR exists to fix (Codex, 2026-10-19).

    Selection runs on the NON-strict read, so one transient Gist failure hands
    content_prep_stage the empty default and it can reselect a story a crashed
    run already claimed. An earlier version replaced that claim with its own,
    passed verification, and reposted the story. With no owner token there is no
    way to prove the claim is ours, so an existing one must refuse.
    """
    events = _events(monkeypatch)
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [
            {"link": "https://a.example.com/x", "stage": "pending", "reserved_at": "t0"}
        ],
    })

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_conflict" in events
    # The other run's claim survives, untouched.
    entries = box["value"]["pending_broadcast"]
    assert len(entries) == 1
    assert entries[0]["reserved_at"] == "t0"


def test_an_uncertain_claim_also_refuses(monkeypatch):
    """Not just `pending` — any live claim blocks acquisition."""
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [{"link": "https://a.example.com/x", "stage": "uncertain"}],
    })

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert state_store.reserved_links(box["value"]) == ["https://a.example.com/x"]


def test_a_story_already_in_links_refuses(monkeypatch):
    """The same stale-selection path can surface a URL already posted."""
    events = _events(monkeypatch)
    box = _state(monkeypatch, {
        "links": ["https://a.example.com/x"],
        "recent_topics": [],
    })

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_conflict" in events
    assert state_store.reserved_links(box["value"]) == []  # no claim written


def test_a_conflict_does_not_release_the_other_runs_claim(monkeypatch):
    """The release paths must not fire on a conflict — that claim is not ours."""
    events = _events(monkeypatch)
    _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [{"link": "https://a.example.com/x", "stage": "pending"}],
    })

    state_store.reserve_broadcast("https://a.example.com/x")

    assert "broadcast_claim_released" not in events
    assert "broadcast_claim_release_failed" not in events


def test_reserving_an_unclaimed_story_still_succeeds(monkeypatch):
    """The conflict check must not block the normal path."""
    box = _state(monkeypatch, {
        "links": ["https://other.example.com/a"],
        "recent_topics": [],
        "pending_broadcast": [{"link": "https://other.example.com/b", "stage": "pending"}],
    })

    assert state_store.reserve_broadcast("https://a.example.com/x") is True
    assert "https://a.example.com/x" in state_store.reserved_links(box["value"])


def test_reservations_are_capped(monkeypatch):
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [
            {"link": f"https://a.example.com/{i}", "stage": "uncertain"}
            for i in range(MAX_BROADCAST_RESERVATIONS + 5)
        ],
    })

    state_store.reserve_broadcast("https://a.example.com/new")

    entries = box["value"]["pending_broadcast"]
    assert len(entries) == MAX_BROADCAST_RESERVATIONS
    assert entries[-1]["link"] == "https://a.example.com/new"


def test_reservation_drops_malformed_entries_on_write(monkeypatch):
    box = _state(monkeypatch, {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": ["junk", {}, {"link": "https://keep.example.com/a"}],
    })

    state_store.reserve_broadcast("https://a.example.com/x")

    assert state_store.reserved_links(box["value"]) == [
        "https://keep.example.com/a",
        "https://a.example.com/x",
    ]


# ---------------------------------------------------------------------------
# save_seen_articles now names its tier
# ---------------------------------------------------------------------------

def test_save_reports_the_local_tier(monkeypatch, tmp_path):
    _local_only(monkeypatch, tmp_path)
    assert state_store.save_seen_articles({"links": [], "recent_topics": []}) == "local"


def test_save_reports_none_when_even_the_local_write_fails(monkeypatch, tmp_path):
    _local_only(monkeypatch, tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(state_store, "_atomic_write_json", _boom)
    assert state_store.save_seen_articles({"links": [], "recent_topics": []}) is None


def test_untrusted_read_reports_no_storage_tier(monkeypatch):
    """An untrusted read skips the write, and the checked variant must say so —
    otherwise a reservation would believe a claim that was never written."""
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": []}, False, state_store.GIST_READ_UNREACHABLE),
    )
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())

    state, tier = state_store.update_seen_articles_checked(lambda c: c)

    assert tier is None


# ---------------------------------------------------------------------------
# gist_status: what the aggregate trust flag cannot express
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# require_gist_read: a reservation must not decide blind and then overwrite
# ---------------------------------------------------------------------------

def test_a_reservation_write_is_skipped_when_the_gist_did_not_answer(monkeypatch):
    """The hole `require_gist_read` closes (Codex, 2026-10-19).

    `update_seen_articles_checked` writes on the AGGREGATE trust flag, so a
    failed Gist GET with a healthy store still permits one. The mutator then
    makes its decision against store state the Gist cannot be seen into — "is
    this URL already claimed?" answered from a tier that cannot know — and the
    write PATCHes that straight over the Gist, erasing the very claim that
    would have said no. Another run's reservation is replaced with ours and the
    story goes out twice: the hole 09a62ee closed on #198, through a different
    door.
    """
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())
    saved = []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d) or "gist")
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        # Trusted — the store answered with real state. But it is state that
        # cannot see the claim another run put in the Gist.
        lambda: ({"links": [], "recent_topics": []}, True, state_store.GIST_READ_UNREACHABLE),
    )

    _state_out, tier = state_store.update_seen_articles_checked(
        lambda c: c, require_gist_read=True
    )

    assert tier is None
    assert saved == []  # nothing was written over the Gist


def test_a_corrupt_gist_also_blocks_a_reservation_write(monkeypatch):
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())
    saved = []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d) or "gist")
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": [], "recent_topics": []}, True, state_store.GIST_READ_CORRUPT),
    )

    _state_out, tier = state_store.update_seen_articles_checked(
        lambda c: c, require_gist_read=True
    )

    assert tier is None
    assert saved == []


def _levelled_events(monkeypatch):
    """Event names WITH their level, which `_events` folds away.

    Needed because the corrupt-Gist overwrite has one event name rather than a
    pair, so the level is the only thing that says whether it is loud.
    """
    seen = []
    for level in ("info", "warn", "error"):
        monkeypatch.setattr(
            state_store.SafeLogger,
            level,
            lambda event, *a, _lvl=level, **k: seen.append((_lvl, event)),
        )
    return seen


def test_a_corrupt_gist_is_written_over_when_the_caller_vouches_for_it(monkeypatch):
    """The one status whose premise the guard gets wrong (#201).

    Reads fall straight past unparseable content, so the entries the guard is
    protecting are entries nothing can return — and withholding a SUPPRESSING
    write on their behalf leaves nothing suppressing the story at all.
    """
    levelled = _levelled_events(monkeypatch)
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())
    saved = []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d) or "gist")
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": ["a"], "recent_topics": []}, True, state_store.GIST_READ_CORRUPT),
    )

    _state_out, tier = state_store.update_seen_articles_checked(
        lambda c: c, require_gist_read=True, corrupt_gist_is_inert=True
    )

    assert tier == "gist"
    assert len(saved) == 1
    # Loud, although the write worked: the Gist was corrupt, and this write has
    # just replaced whatever it held.
    assert ("error", "seen_articles_update_over_corrupt_gist") in levelled


def test_an_unreachable_gist_blocks_the_write_even_for_an_inert_caller(monkeypatch):
    """What keeps the two statuses apart: UNREACHABLE content is unknown, not
    known to be unusable, and it answers reads again the moment it is back."""
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())
    saved = []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d) or "gist")
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": [], "recent_topics": []}, True, state_store.GIST_READ_UNREACHABLE),
    )

    _state_out, tier = state_store.update_seen_articles_checked(
        lambda c: c, require_gist_read=True, corrupt_gist_is_inert=True
    )

    assert tier is None
    assert saved == []


def test_an_untrusted_read_blocks_the_write_whatever_the_caller_vouches_for(monkeypatch):
    """The inert flag narrows the GIST guard only. An untrusted read means no
    tier supplied real state, so the mutator is handed the empty default and the
    write would replace every seen link, recent topic and Pioneer cooldown with
    nothing — the older and larger hole, and still closed."""
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())
    saved = []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d) or "gist")
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": [], "recent_topics": []}, False, state_store.GIST_READ_CORRUPT),
    )

    _state_out, tier = state_store.update_seen_articles_checked(
        lambda c: c, require_gist_read=True, corrupt_gist_is_inert=True
    )

    assert tier is None
    assert saved == []


def test_bookkeeping_writes_are_not_subject_to_the_gist_requirement(monkeypatch):
    """Opt-in, because it is only worth a missed write where the mutator's
    decision depends on state the Gist might hold alone. The default path keeps
    writing on aggregate trust, as every non-reservation caller expects."""
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())
    saved = []
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: saved.append(d) or "store")
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": [], "recent_topics": []}, True, state_store.GIST_READ_UNREACHABLE),
    )

    _state_out, tier = state_store.update_seen_articles_checked(lambda c: c)

    assert tier == "store"
    assert len(saved) == 1


def test_no_gist_configured_does_not_block_a_reservation_write(monkeypatch):
    """Local development and store-only deployments read GIST_READ_OK, so the
    guard costs them nothing."""
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())
    monkeypatch.setattr(state_store, "save_seen_articles", lambda d: "local")
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: ({"links": [], "recent_topics": []}, True, state_store.GIST_READ_OK),
    )

    _state_out, tier = state_store.update_seen_articles_checked(
        lambda c: c, require_gist_read=True
    )

    assert tier == "local"


def test_the_three_reservation_entry_points_require_the_gist_read(monkeypatch):
    """Pinned as a set, so a fourth entry point or a dropped flag is visible."""
    seen = []

    def _fake(mutator, **kw):
        seen.append(kw.get("require_gist_read", False))
        return mutator({"links": [], "recent_topics": [], "pending_broadcast": []}), "gist"

    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_OK,
        ),
    )

    state_store.reserve_broadcast("https://a.example.com/x")
    state_store._release_claim("https://a.example.com/x", reason="test")
    state_store.settle_broadcast("https://a.example.com/x", delivered=True)

    assert seen == [True, True, True]

def test_only_the_suppressing_settlement_may_write_over_a_corrupt_gist(monkeypatch):
    """Which callers set `corrupt_gist_is_inert`, pinned as a set (#201).

    This is the whole of the fix at the call-site level: the two suppressing
    settlement outcomes may overwrite corrupt content, the reservation and both
    releases may not.
    """
    seen = []

    def _fake(mutator, **kw):
        seen.append(kw.get("corrupt_gist_is_inert", False))
        return mutator({"links": [], "recent_topics": [], "pending_broadcast": []}), "gist"

    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)
    monkeypatch.setattr(
        state_store,
        "load_seen_articles_sourced",
        lambda: (
            {
                "links": [],
                "recent_topics": [],
                "pending_broadcast": [
                    {"link": "https://a.example.com/x", "stage": "pending"}
                ],
            },
            True,
            state_store.GIST_READ_OK,
        ),
    )
    link = "https://a.example.com/x"

    state_store.reserve_broadcast(link)
    state_store._release_claim(link, reason="test")
    state_store.settle_broadcast(link, delivered=True)
    state_store.settle_broadcast(link, delivered=False, attempted=True)
    state_store.settle_broadcast(link, delivered=False, attempted=False)

    assert seen == [False, False, True, True, False]



class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_the_gist_read_calls_unparseable_content_corrupt(monkeypatch):
    """Pinned on the real read, not a stub of it: the status has to be derived
    from what actually came back, or the branch above it is decoration."""
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setattr(
        state_store.httpx,
        "get",
        lambda *a, **k: _Resp({"files": {"seen_articles.json": {"content": "{not json"}}}),
    )

    value, status = state_store._load_gist_state_checked("seen_articles.json")

    assert value is None
    assert status == state_store.GIST_READ_CORRUPT


def test_the_gist_read_calls_a_failed_request_unreachable(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setattr(state_store.httpx, "get", _boom)

    value, status = state_store._load_gist_state_checked("seen_articles.json")

    assert value is None
    assert status == state_store.GIST_READ_UNREACHABLE


def test_the_gist_read_calls_a_missing_file_ok(monkeypatch):
    """Reachable with nothing stored is an answer, not a failure."""
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.setattr(state_store.httpx, "get", lambda *a, **k: _Resp({"files": {}}))

    value, status = state_store._load_gist_state_checked("seen_articles.json")

    assert value is None
    assert status == state_store.GIST_READ_OK


def test_the_gist_read_calls_no_gist_configured_ok(monkeypatch):
    monkeypatch.delenv("GIST_ID", raising=False)

    assert state_store._load_gist_state_checked("seen_articles.json") == (
        None,
        state_store.GIST_READ_OK,
    )


def test_a_store_answer_does_not_settle_a_silent_gist(monkeypatch):
    """The loader-level fact the reservation depends on (Codex, 2026-10-19).

    A failed Gist GET with a healthy store yields `trusted=True` — real state
    did come back — but the Gist tier never answered, so nothing read below it
    survives the Gist recovering. Only the status carries that.
    """
    monkeypatch.setattr(
        state_store,
        "_load_gist_state_checked",
        lambda _f: (None, state_store.GIST_READ_UNREACHABLE),
    )
    monkeypatch.setattr(
        state_store,
        "_load_state_from_store",
        lambda _k: {"links": ["https://a.example.com/x"], "recent_topics": []},
    )

    state, trusted, gist_status = state_store.load_seen_articles_sourced()

    assert state["links"] == ["https://a.example.com/x"]
    assert trusted is True
    assert gist_status == state_store.GIST_READ_UNREACHABLE


def test_corrupt_gist_content_is_reported_as_corrupt_not_unreachable(monkeypatch):
    """The two reasons a Gist read is unusable are not the same fact
    (Codex, 2026-10-19).

    Unreachable is the absence of evidence — the content is unknown and may
    well be what we just wrote. Corrupt is evidence: the Gist answered, what it
    holds cannot be parsed, so every read falls PAST it and anything written
    there is invisible until it is fixed.
    """
    monkeypatch.setattr(
        state_store,
        "_load_gist_state_checked",
        lambda _f: (None, state_store.GIST_READ_CORRUPT),
    )
    monkeypatch.setattr(
        state_store, "_load_state_from_store", lambda _k: {"links": [], "recent_topics": []}
    )

    _state_out, trusted, gist_status = state_store.load_seen_articles_sourced()

    assert trusted is True
    assert gist_status == state_store.GIST_READ_CORRUPT


def test_a_gist_with_no_file_is_settled(monkeypatch):
    """"Reachable, nothing stored" is an answer; the store below it is then
    the tier reads legitimately land on, now and next run."""
    monkeypatch.setattr(
        state_store, "_load_gist_state_checked", lambda _f: (None, state_store.GIST_READ_OK)
    )
    monkeypatch.setattr(
        state_store, "_load_state_from_store", lambda _k: {"links": [], "recent_topics": []}
    )

    _state_out, trusted, gist_status = state_store.load_seen_articles_sourced()

    assert trusted is True
    assert gist_status == state_store.GIST_READ_OK


def test_a_gist_that_answers_settles_itself(monkeypatch):
    monkeypatch.setattr(
        state_store,
        "_load_gist_state_checked",
        lambda _f: ({"links": [], "recent_topics": []}, state_store.GIST_READ_OK),
    )

    _state_out, trusted, gist_status = state_store.load_seen_articles_sourced()

    assert trusted is True
    assert gist_status == state_store.GIST_READ_OK


def test_the_strict_wrapper_still_reports_only_the_aggregate(monkeypatch):
    """`load_seen_articles_strict` keeps its two-value contract for the six
    callers outside this module — the status is additive, not a change of
    meaning."""
    monkeypatch.setattr(
        state_store,
        "_load_gist_state_checked",
        lambda _f: (None, state_store.GIST_READ_UNREACHABLE),
    )
    monkeypatch.setattr(
        state_store, "_load_state_from_store", lambda _k: {"links": [], "recent_topics": []}
    )

    assert state_store.load_seen_articles_strict()[1] is True


@pytest.mark.parametrize(
    "status,expected",
    [
        (state_store.GIST_READ_OK, True),
        (state_store.GIST_READ_UNREACHABLE, False),
        (state_store.GIST_READ_CORRUPT, False),
    ],
)
def test_the_gist_strict_wrapper_folds_both_failures_to_false(monkeypatch, status, expected):
    """Both failure statuses stay `trusted=False` for the writers that ask
    "may I overwrite what is there?" — the distinction is only for readers."""
    monkeypatch.setattr(state_store, "_load_gist_state_checked", lambda _f: (None, status))

    assert state_store._load_gist_state_strict("seen_articles.json")[1] is expected


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
