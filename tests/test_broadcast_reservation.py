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
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    monkeypatch.delenv("STATE_STORE_URL", raising=False)
    box = {"value": dict(initial), "tier": "gist"}

    def _fake(mutator):
        box["value"] = mutator(box["value"])
        return box["value"], box["tier"]

    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)
    monkeypatch.setattr(state_store, "load_seen_articles", lambda: box["value"])
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

    def _fake(mutator):
        return {}, None

    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)

    state_store.settle_broadcast("https://a.example.com/x", delivered=True)

    assert "broadcast_settlement_failed" in events
    assert "broadcast_settled" not in events


# ---------------------------------------------------------------------------
# What counts as a stored claim
# ---------------------------------------------------------------------------

def test_reservation_fails_when_no_tier_stored_it(monkeypatch):
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store, "update_seen_articles_checked", lambda m: ({}, None))

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_failed" in events


def test_reservation_fails_when_only_the_local_file_took_it_on_a_runner(monkeypatch):
    """A remote tier is configured and refused: the local file dies with the
    GitHub Actions runner, so calling that stored would be a false positive."""
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m: (m({}), "local")
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
        state_store, "update_seen_articles_checked", lambda m: (m({}), "store")
    )
    # What a later read comes back with: the stale Gist, no claim in it.
    monkeypatch.setattr(
        state_store, "load_seen_articles", lambda: {"links": [], "recent_topics": []}
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
        state_store, "update_seen_articles_checked", lambda m: (m({}), "store")
    )
    monkeypatch.setattr(
        state_store,
        "load_seen_articles",
        lambda: {
            "links": [],
            "recent_topics": [],
            "pending_broadcast": [{"link": "https://a.example.com/x", "stage": "pending"}],
        },
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is True


def test_a_refused_reservation_releases_the_claim_it_wrote(monkeypatch):
    """Write lands, read-back fails transiently (Codex, 2026-10-19).

    The caller aborts the broadcast and never reaches settlement, so a claim left
    behind here would suppress — forever, since claims are not pruned by age — a
    story that definitively never posted.
    """
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    state = {
        "links": [],
        "recent_topics": [],
        "pending_broadcast": [],
    }

    def _fake(mutator):
        return mutator(state), "gist"

    monkeypatch.setattr(state_store, "update_seen_articles_checked", _fake)
    # The verification read fails: it sees state without the claim.
    monkeypatch.setattr(
        state_store, "load_seen_articles", lambda: {"links": [], "recent_topics": []}
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_reservation_not_readable" in events
    assert "broadcast_claim_released" in events
    # The story is back in the pool, not silently lost.
    assert state_store.reserved_links(state) == []


def test_a_non_durable_reservation_also_releases_its_claim(monkeypatch):
    """Same cleanup on the ephemeral-runner rejection."""
    monkeypatch.setenv("GIST_ID", "abc123")
    events = _events(monkeypatch)
    state = {"links": [], "recent_topics": [], "pending_broadcast": []}
    monkeypatch.setattr(
        state_store, "update_seen_articles_checked", lambda m: (m(state), "local")
    )

    assert state_store.reserve_broadcast("https://a.example.com/x") is False
    assert "broadcast_claim_released" in events
    assert state_store.reserved_links(state) == []


def test_a_failed_release_is_loud(monkeypatch):
    """If the cleanup write is refused too, the story IS stuck — say so."""
    events = _events(monkeypatch)
    monkeypatch.setattr(state_store, "update_seen_articles_checked", lambda m: ({}, None))

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
        state_store, "update_seen_articles_checked", lambda m: (m(state), "gist")
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


def test_reserving_the_same_story_twice_keeps_one_claim(monkeypatch):
    box = _state(monkeypatch, {"links": [], "recent_topics": []})

    state_store.reserve_broadcast("https://a.example.com/x")
    state_store.reserve_broadcast("https://a.example.com/x")

    assert state_store.reserved_links(box["value"]) == ["https://a.example.com/x"]


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
    monkeypatch.setattr(state_store, "load_seen_articles_strict", lambda: ({"links": []}, False))
    monkeypatch.setattr(state_store, "_file_lock", lambda path: _NullLock())

    state, tier = state_store.update_seen_articles_checked(lambda c: c)

    assert tier is None


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
