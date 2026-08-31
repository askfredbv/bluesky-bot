from types import SimpleNamespace

import pytest

import main


@pytest.mark.asyncio
async def test_mode_selection_stage_selects_curator(monkeypatch):
    class FixedDatetime:
        @classmethod
        def now(cls, tz=None):
            return SimpleNamespace(hour=9)

    monkeypatch.setattr(main, "datetime", FixedDatetime)

    payload = await main.mode_selection_stage()

    assert payload.mode == "curator"
    assert payload.current_hour_utc == 9


@pytest.mark.asyncio
async def test_mode_selection_stage_honors_force_mode(monkeypatch):
    class FixedDatetime:
        @classmethod
        def now(cls, tz=None):
            return SimpleNamespace(hour=9)  # would be Curator by the clock

    monkeypatch.setattr(main, "datetime", FixedDatetime)
    monkeypatch.setenv("FORCE_MODE", "strategist")

    payload = await main.mode_selection_stage()

    assert payload.mode == "strategist"  # FORCE_MODE overrides the hour


@pytest.mark.asyncio
async def test_mode_selection_stage_ignores_blank_force_mode(monkeypatch):
    class FixedDatetime:
        @classmethod
        def now(cls, tz=None):
            return SimpleNamespace(hour=14)  # Mentor by the clock

    monkeypatch.setattr(main, "datetime", FixedDatetime)
    monkeypatch.setenv("FORCE_MODE", "")  # blank => normal hour-based selection

    payload = await main.mode_selection_stage()

    assert payload.mode == "mentor"


def test_should_attempt_image_never_for_curator(monkeypatch):
    monkeypatch.setattr(main.random, "random", lambda: 0.0)  # would pass the roll
    assert main._should_attempt_image(main.Mode.CURATOR, force_image=True) is False
    assert main._should_attempt_image(main.Mode.CURATOR, force_image=False) is False


def test_should_attempt_image_forced_overrides_probability(monkeypatch):
    monkeypatch.setattr(main.random, "random", lambda: 0.99)  # would fail the roll
    assert main._should_attempt_image(main.Mode.MENTOR, force_image=True) is True


def test_should_attempt_image_probability_gate(monkeypatch):
    monkeypatch.setattr(main, "IMAGE_GENERATION_PROBABILITY", 0.5)
    monkeypatch.setattr(main.random, "random", lambda: 0.4)  # below the gate
    assert main._should_attempt_image(main.Mode.MENTOR, force_image=False) is True
    monkeypatch.setattr(main.random, "random", lambda: 0.6)  # above the gate
    assert main._should_attempt_image(main.Mode.MENTOR, force_image=False) is False


@pytest.mark.asyncio
async def test_content_prep_stage_degrades_mode_when_news_is_low(monkeypatch):
    creds = SimpleNamespace(bluesky_username="u", bluesky_password="p")

    class FakeClient:
        def on_session_change(self, cb): return cb
        async def login(self, username, password):
            return None

    async def fake_get_recent_posts(*args, **kwargs):
        return ["a", "b"]

    async def fake_fetch_news(*args, **kwargs):
        return [{"link": "https://one"}]

    monkeypatch.setattr(main, "load_seen_articles", lambda: {"links": [], "recent_topics": []})
    monkeypatch.setattr(main, "fetch_news", fake_fetch_news)
    monkeypatch.setattr(main, "get_link_metadata", lambda *_: {"title": "unused"})
    monkeypatch.setattr(main, "get_recent_posts", fake_get_recent_posts)
    monkeypatch.setitem(__import__("sys").modules, "atproto", SimpleNamespace(AsyncClient=FakeClient))

    payload = await main.content_prep_stage(main.ModeSelectionPayload(mode="curator", current_hour_utc=8), creds)

    assert payload.mode == "strategist"
    assert payload.news_items == [{"link": "https://one"}]
    assert payload.recent_posts == ["a", "b"]
    assert payload.link_meta is None


def _patch_gen(monkeypatch, gen_calls, returns=b"rawbytes"):
    async def counting_generate_image(*a, **k):
        gen_calls.append(1)
        return returns
    monkeypatch.setattr(main, "generate_post_image", counting_generate_image)
    monkeypatch.setattr(main, "compress_image", lambda b, **k: b"compressed")
    monkeypatch.setattr(main, "is_usable_image", lambda *a, **k: True)


@pytest.mark.asyncio
async def test_curator_fallback_generates_when_no_thumbnail(monkeypatch):
    gen: list = []
    _patch_gen(monkeypatch, gen)
    link_meta = {"title": "Big AI launch", "image_data": None}

    result = await main._apply_curator_fallback_image(link_meta, "key", "Big AI launch")

    assert gen == [1]                              # fallback fired
    assert link_meta["image_data"] == b"compressed"
    assert result == b"compressed"                 # returned for Mastodon parity


@pytest.mark.asyncio
async def test_curator_fallback_keeps_existing_thumbnail(monkeypatch):
    gen: list = []
    _patch_gen(monkeypatch, gen)
    link_meta = {"title": "T", "image_data": b"og-thumb"}

    result = await main._apply_curator_fallback_image(link_meta, "key", "T")

    assert gen == []                               # no generation when a thumbnail exists
    assert link_meta["image_data"] == b"og-thumb"
    assert result is None                          # nothing generated -> nothing for Mastodon


@pytest.mark.asyncio
async def test_curator_fallback_no_image_when_generation_empty(monkeypatch):
    gen: list = []
    _patch_gen(monkeypatch, gen, returns=None)     # generation returns nothing
    link_meta = {"title": "T", "image_data": None}

    result = await main._apply_curator_fallback_image(link_meta, "key", "T")

    assert link_meta["image_data"] is None         # thumbnail-free card, no crash
    assert result is None


@pytest.mark.asyncio
async def test_curator_fallback_survives_compression_failure(monkeypatch):
    gen: list = []
    _patch_gen(monkeypatch, gen)

    def boom(_b, **_k):
        raise ValueError("cannot encode LA png as JPEG")
    monkeypatch.setattr(main, "compress_image", boom)
    link_meta = {"title": "T", "image_data": None}

    result = await main._apply_curator_fallback_image(link_meta, "key", "T")

    assert link_meta["image_data"] is None         # compression failed -> thumbnail-free, no crash
    assert result is None


@pytest.mark.asyncio
async def test_curator_fallback_rejects_unusable_image(monkeypatch):
    gen: list = []
    _patch_gen(monkeypatch, gen)
    # compress_image returns bytes Pillow can't open (its own open-failure path),
    # so validation must reject them rather than shipping invalid media.
    monkeypatch.setattr(main, "is_usable_image", lambda *a, **k: False)
    link_meta = {"title": "T", "image_data": None}

    result = await main._apply_curator_fallback_image(link_meta, "key", "T")

    assert result is None                          # unusable -> nothing shipped
    assert link_meta["image_data"] is None         # thumbnail-free card


@pytest.mark.asyncio
async def test_broadcasting_stage_uses_fallback_client_when_bluesky_task_fails(monkeypatch):
    creds = SimpleNamespace(
        gemini_api_key="g",
        bluesky_username="u",
        bluesky_password="p",
        mastodon_access_token="t",
        mastodon_api_base_url="https://masto",
    )
    settings = SimpleNamespace(platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0))

    async def fake_generate_content(*args, **kwargs):
        return ["hello"], "topic", None

    async def failing_bluesky(*args, **kwargs):
        raise RuntimeError("fail")

    async def ok_mastodon(*args, **kwargs):
        from src.metrics import BroadcastResult
        return BroadcastResult(client=None, sent_uris=["123"])

    async def no_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "generate_content", fake_generate_content)
    monkeypatch.setattr(main, "post_to_bluesky", failing_bluesky)
    monkeypatch.setattr(main, "post_to_mastodon", ok_mastodon)
    monkeypatch.setattr(main, "apply_humanized_post_delay", no_delay)
    monkeypatch.setattr(main.random, "choice", lambda seq: "default")

    prep = main.ContentPrepPayload(
        mode="mentor",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        link_meta=None,
        bsky_client="fallback-client",
        recent_posts=[],
    )

    payload = await main.broadcasting_stage(prep, settings, creds)

    assert payload.bsky_broadcast_client == "fallback-client"
    assert payload.content_list == ["hello"]


@pytest.mark.asyncio
async def test_post_run_automation_stage_runs_expected_tasks(monkeypatch):
    calls = {"handle": 0}
    creds = SimpleNamespace(
        bluesky_username="u",
        gemini_api_key="g",
        mastodon_access_token="t",
        mastodon_api_base_url="https://masto",
    )

    async def fake_handle(*args, **kwargs):
        calls["handle"] += 1

    monkeypatch.setattr(main, "handle_interactions", fake_handle)

    broadcast = main.BroadcastPayload(
        mode="mentor",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        content_list=["x"],
        chosen_topic="y",
        thread_pause_profile="z",
        bsky_broadcast_client="client",
    )

    payload = await main.post_run_automation_stage(broadcast, creds)

    assert calls["handle"] == 1
    assert payload.mode == "mentor"


@pytest.mark.asyncio
async def test_persistence_stage_updates_seen_articles(monkeypatch):
    state = {"links": ["old"], "recent_topics": ["AI"]}
    updated = {}

    def fake_update(transform):
        updated["value"] = transform({})

    monkeypatch.setattr(main, "update_seen_articles", fake_update)

    payload = main.AutomationPayload(
        mode="curator",
        seen_data=state,
        news_items=[{"link": "https://new", "detected_topic": "Markets"}],
    )

    await main.persistence_stage(payload)

    assert updated["value"]["links"][-1] == "https://new"
    assert updated["value"]["recent_topics"][-1] == "Markets"


@pytest.mark.asyncio
async def test_main_smoke_orchestrates_stage_pipeline(monkeypatch):
    calls = []
    settings = SimpleNamespace(credentials=SimpleNamespace(gemini_api_key="test-key"), platform=SimpleNamespace())

    async def fake_mode():
        calls.append("mode")
        return main.ModeSelectionPayload(mode="mentor", current_hour_utc=13)

    async def fake_content(mode_payload, creds):
        calls.append(("content", mode_payload.mode, creds is settings.credentials))
        return main.ContentPrepPayload(
            mode="mentor",
            seen_data={"links": [], "recent_topics": []},
            news_items=[],
            link_meta=None,
            bsky_client="client",
            recent_posts=[],
        )

    async def fake_broadcast(content_prep, incoming_settings, creds, active_models=None):
        calls.append(("broadcast", content_prep.mode, incoming_settings is settings, creds is settings.credentials))
        return main.BroadcastPayload(
            mode="mentor",
            seen_data={"links": [], "recent_topics": []},
            news_items=[],
            content_list=["post"],
            chosen_topic="topic",
            thread_pause_profile="default",
            bsky_broadcast_client="client",
        )

    async def fake_automation(broadcast_payload, creds):
        calls.append(("automation", broadcast_payload.mode, creds is settings.credentials))
        return main.AutomationPayload(mode="mentor", seen_data={"links": [], "recent_topics": []}, news_items=[])

    async def fake_persistence(automation_payload):
        calls.append(("persistence", automation_payload.mode))

    async def fake_filter_models(api_key, priority):
        return priority

    monkeypatch.setattr(main, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(main, "filter_available_models", fake_filter_models)
    monkeypatch.setattr(main, "mode_selection_stage", fake_mode)
    monkeypatch.setattr(main, "content_prep_stage", fake_content)
    monkeypatch.setattr(main, "broadcasting_stage", fake_broadcast)
    monkeypatch.setattr(main, "post_run_automation_stage", fake_automation)
    monkeypatch.setattr(main, "persistence_stage", fake_persistence)

    await main.main()

    assert calls == [
        "mode",
        ("content", "mentor", True),
        ("broadcast", "mentor", True, True),
        ("automation", "mentor", True),
        ("persistence", "mentor"),
    ]


@pytest.mark.asyncio
async def test_capture_post_metrics_stage_writes_one_row_per_sent_uri(monkeypatch):
    """One BroadcastPayload with 2 Bluesky URIs and 1 Mastodon ID should
    record three rows total, each carrying the metrics_context fields."""
    saved = {}
    monkeypatch.setattr("src.metrics.load_post_metrics", lambda: {"posts": []})
    monkeypatch.setattr("src.metrics.save_post_metrics", lambda d: saved.update(data=d))

    async def fake_refresh(post_metrics, bsky_client, mastodon_client, now):
        return {"bluesky": 0, "mastodon": 0, "skipped": 0, "errors": 0}

    monkeypatch.setattr("src.metrics.refresh_stale_metrics", fake_refresh)
    monkeypatch.setattr("src.metrics.prune_old_metrics", lambda data, now: 0)

    broadcast = main.BroadcastPayload(
        mode="curator",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        content_list=["post-1", "post-2"],
        chosen_topic="LLMs",
        thread_pause_profile="default",
        bsky_broadcast_client=None,
        bsky_sent_uris=["at://post/1", "at://post/2"],
        mastodon_sent_ids=["1000"],
        metrics_context={
            "mode": "curator",
            "topic": "LLMs",
            "source_domain": "openai.com",
            "pioneer_id": None,
            "had_image": False,
            "had_link_card": True,
        },
    )
    creds = SimpleNamespace(mastodon_access_token=None, mastodon_api_base_url="https://x")

    await main.capture_post_metrics_stage(broadcast, creds)

    rows = saved["data"]["posts"]
    assert len(rows) == 3
    assert [r["platform"] for r in rows] == ["bluesky", "bluesky", "mastodon"]
    assert [r["thread_position"] for r in rows] == [0, 1, 0]
    assert all(r["topic"] == "LLMs" for r in rows)
    assert all(r["source_domain"] == "openai.com" for r in rows)
    assert all(r["had_link_card"] is True for r in rows)
    assert rows[0]["content_preview"] == "post-1"
    assert rows[1]["content_preview"] == "post-2"
    assert rows[2]["content_preview"] == "post-1"


@pytest.mark.asyncio
async def test_capture_post_metrics_stage_still_runs_refresh_when_nothing_sent(monkeypatch):
    """Empty sent_uris should still trigger the refresh+prune pass —
    Step 5 needs to keep refreshing prior rows even on a run where nothing
    new was posted (e.g. invariant_violated)."""
    save_calls = []
    monkeypatch.setattr("src.metrics.load_post_metrics", lambda: {"posts": []})
    monkeypatch.setattr("src.metrics.save_post_metrics", lambda d: save_calls.append(d))

    refresh_calls = []

    async def fake_refresh(post_metrics, bsky_client, mastodon_client, now):
        refresh_calls.append(True)
        return {"bluesky": 0, "mastodon": 0, "skipped": 0, "errors": 0}

    monkeypatch.setattr("src.metrics.refresh_stale_metrics", fake_refresh)
    monkeypatch.setattr("src.metrics.prune_old_metrics", lambda data, now: 0)

    broadcast = main.BroadcastPayload(
        mode="mentor",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        content_list=["unused"],
        chosen_topic="topic",
        thread_pause_profile="default",
        bsky_broadcast_client=None,
    )
    creds = SimpleNamespace(mastodon_access_token=None, mastodon_api_base_url="https://x")
    await main.capture_post_metrics_stage(broadcast, creds)

    assert refresh_calls == [True]
    assert len(save_calls) == 1


@pytest.mark.asyncio
async def test_broadcasting_stage_skips_post_when_generate_returns_empty(monkeypatch):
    """v4.18: if generate_content returns ([], topic), broadcasting_stage
    must skip the broadcast entirely — not call post_to_bluesky / post_to_mastodon.
    This is the safety net that catches the model-chain-exhaustion case
    that previously shipped "Notes on X — more soon." stub posts."""
    posts_attempted = {"bsky": 0, "mastodon": 0}

    async def fake_generate(*args, **kwargs):
        return [], "exhausted topic", None

    async def should_not_be_called_bsky(*args, **kwargs):
        posts_attempted["bsky"] += 1
        raise AssertionError("post_to_bluesky must not be called on empty content")

    async def should_not_be_called_mastodon(*args, **kwargs):
        posts_attempted["mastodon"] += 1
        raise AssertionError("post_to_mastodon must not be called on empty content")

    async def no_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "generate_content", fake_generate)
    monkeypatch.setattr(main, "post_to_bluesky", should_not_be_called_bsky)
    monkeypatch.setattr(main, "post_to_mastodon", should_not_be_called_mastodon)
    monkeypatch.setattr(main, "apply_humanized_post_delay", no_delay)

    creds = SimpleNamespace(
        gemini_api_key="g",
        bluesky_username="u",
        bluesky_password="p",
        mastodon_access_token="t",
        mastodon_api_base_url="https://masto",
    )
    settings = SimpleNamespace(platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0))
    prep = main.ContentPrepPayload(
        mode="mentor",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        link_meta=None,
        bsky_client="some-client",
        recent_posts=[],
    )

    payload = await main.broadcasting_stage(prep, settings, creds)

    assert payload.content_list == []
    assert payload.bsky_sent_uris == []
    assert payload.mastodon_sent_ids == []
    assert posts_attempted == {"bsky": 0, "mastodon": 0}


@pytest.mark.asyncio
async def test_broadcasting_stage_curator_link_card_follows_chosen_item(monkeypatch):
    """v4.21: when the Curator picks a non-top item, broadcasting_stage
    refetches the link card from the chosen URL and records that item's
    domain in metrics_context — not the pre-fetched news_items[0] card."""
    fetched = {}
    captured_link_meta = {}

    async def fake_generate(*args, **kwargs):
        return ["a post"], "Chosen Topic", "https://chosen.example.com/article"

    async def fake_get_link_metadata(url):
        fetched["url"] = url
        return {"title": "Chosen", "description": "", "image_data": None, "url": url}

    async def fake_post_to_bluesky(client, content_list, link_meta, **kwargs):
        captured_link_meta["value"] = link_meta
        from src.metrics import BroadcastResult
        return BroadcastResult(client=client, sent_uris=["at://x"])

    async def fake_post_to_mastodon(*args, **kwargs):
        from src.metrics import BroadcastResult
        return BroadcastResult(client=None, sent_uris=["1"])

    async def no_delay(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "generate_content", fake_generate)
    monkeypatch.setattr(main, "get_link_metadata", fake_get_link_metadata)
    monkeypatch.setattr(main, "post_to_bluesky", fake_post_to_bluesky)
    monkeypatch.setattr(main, "post_to_mastodon", fake_post_to_mastodon)
    monkeypatch.setattr(main, "apply_humanized_post_delay", no_delay)
    monkeypatch.setattr(main.random, "choice", lambda seq: list(seq)[0])

    creds = SimpleNamespace(
        gemini_api_key="g",
        bluesky_username="u",
        bluesky_password="p",
        mastodon_access_token="t",
        mastodon_api_base_url="https://masto",
    )
    settings = SimpleNamespace(platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0))
    prep = main.ContentPrepPayload(
        mode="curator",
        seen_data={"links": [], "recent_topics": []},
        news_items=[{"title": "Top", "link": "https://top.example.com/article"}],
        link_meta={"title": "Top", "description": "", "image_data": None, "url": "https://top.example.com/article"},
        bsky_client="some-client",
        recent_posts=[],
        source_domain="top.example.com",
    )

    payload = await main.broadcasting_stage(prep, settings, creds)

    # Card refetched from the chosen URL, not the pre-fetched top item.
    assert fetched["url"] == "https://chosen.example.com/article"
    assert captured_link_meta["value"]["url"] == "https://chosen.example.com/article"
    # Metrics attribution reflects the chosen item's domain.
    assert payload.metrics_context["source_domain"] == "chosen.example.com"


@pytest.mark.asyncio
async def test_broadcasting_stage_curator_fallback_image_is_recorded_and_reaches_mastodon(monkeypatch):
    """A generated Curator fallback image ships to Mastodon and is counted in
    metrics (had_image), not just set as the Bluesky card thumbnail."""
    masto_kwargs = {}

    async def fake_generate(*a, **k):
        return ["a post"], "Chosen Topic", None  # no chosen link -> keep the top card

    async def fake_gen_image(*a, **k):
        return b"rawbytes"                        # override the conftest stub

    async def fake_bluesky(*a, **k):
        from src.metrics import BroadcastResult
        return BroadcastResult(client=None, sent_uris=["at://x"])

    async def fake_mastodon(access_token, api_base_url, content_list, **kwargs):
        masto_kwargs.update(kwargs)
        from src.metrics import BroadcastResult
        return BroadcastResult(client=None, sent_uris=["1"])

    async def no_delay(*a, **k):
        return None

    monkeypatch.setattr(main, "generate_content", fake_generate)
    monkeypatch.setattr(main, "generate_post_image", fake_gen_image)
    monkeypatch.setattr(main, "compress_image", lambda b, **k: b"compressed")
    monkeypatch.setattr(main, "is_usable_image", lambda *a, **k: True)
    monkeypatch.setattr(main, "post_to_bluesky", fake_bluesky)
    monkeypatch.setattr(main, "post_to_mastodon", fake_mastodon)
    monkeypatch.setattr(main, "apply_humanized_post_delay", no_delay)
    monkeypatch.setattr(main.random, "choice", lambda seq: list(seq)[0])

    creds = SimpleNamespace(gemini_api_key="g", bluesky_username="u", bluesky_password="p",
                            mastodon_access_token="t", mastodon_api_base_url="https://masto")
    settings = SimpleNamespace(platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0))
    prep = main.ContentPrepPayload(
        mode="curator", seen_data={"links": [], "recent_topics": []},
        news_items=[{"title": "Top", "link": "https://top.example.com/article"}],
        link_meta={"title": "Top", "image_data": None, "url": "https://top.example.com/article"},
        bsky_client="c", recent_posts=[], source_domain="top.example.com")

    payload = await main.broadcasting_stage(prep, settings, creds)

    assert masto_kwargs.get("image_bytes") == b"compressed"   # fallback attached to Mastodon
    assert payload.metrics_context["had_image"] is True        # and counted in metrics


@pytest.mark.asyncio
async def test_capture_follower_snapshot_stage_records_bluesky_and_mastodon(monkeypatch):
    """One run hitting both platforms appends two snapshot rows — one per
    platform — to growth.json. Bluesky uses getProfile; Mastodon uses
    account_verify_credentials. Failures are isolated per-platform."""
    saved = {}
    monkeypatch.setattr("src.metrics.load_growth", lambda: {"snapshots": []})
    monkeypatch.setattr("src.metrics.save_growth", lambda d: saved.update(data=d))

    class FakeProfile:
        followers_count = 26
        follows_count = 35
        posts_count = 137

    class FakeActorNs:
        async def get_profile(self, _params):
            return FakeProfile()

    class FakeBskyNs:
        actor = FakeActorNs()

    class FakeAppNs:
        bsky = FakeBskyNs()

    class FakeBskyClient:
        app = FakeAppNs()

    class FakeMastodon:
        def __init__(self, **kwargs):
            pass

        def account_verify_credentials(self):
            return {
                "followers_count": 12,
                "following_count": 50,
                "statuses_count": 200,
            }

    monkeypatch.setattr("mastodon.Mastodon", FakeMastodon)

    broadcast = main.BroadcastPayload(
        mode="curator",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        content_list=["whatever"],
        chosen_topic="topic",
        thread_pause_profile="default",
        bsky_broadcast_client=FakeBskyClient(),
    )
    creds = SimpleNamespace(
        bluesky_username="askfred.be",
        mastodon_access_token="t",
        mastodon_api_base_url="https://mastodon.social",
    )

    await main.capture_follower_snapshot_stage(broadcast, creds)

    rows = saved["data"]["snapshots"]
    assert len(rows) == 2
    platforms = {r["platform"]: r for r in rows}
    assert platforms["bluesky"]["followers"] == 26
    assert platforms["bluesky"]["follows"] == 35
    assert platforms["bluesky"]["posts"] == 137
    assert platforms["mastodon"]["followers"] == 12
    assert platforms["mastodon"]["follows"] == 50
    assert platforms["mastodon"]["posts"] == 200


@pytest.mark.asyncio
async def test_capture_follower_snapshot_stage_isolates_per_platform_failures(monkeypatch):
    """If Bluesky's getProfile raises, the Mastodon snapshot must still record."""
    saved = {}
    monkeypatch.setattr("src.metrics.load_growth", lambda: {"snapshots": []})
    monkeypatch.setattr("src.metrics.save_growth", lambda d: saved.update(data=d))

    class BrokenBsky:
        class app:
            class bsky:
                class actor:
                    @staticmethod
                    async def get_profile(_params):
                        raise RuntimeError("bluesky api down")

    class FakeMastodon:
        def __init__(self, **kwargs):
            pass

        def account_verify_credentials(self):
            return {"followers_count": 12, "following_count": 50, "statuses_count": 200}

    monkeypatch.setattr("mastodon.Mastodon", FakeMastodon)

    broadcast = main.BroadcastPayload(
        mode="mentor",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        content_list=["x"],
        chosen_topic="t",
        thread_pause_profile="default",
        bsky_broadcast_client=BrokenBsky(),
    )
    creds = SimpleNamespace(
        bluesky_username="askfred.be",
        mastodon_access_token="t",
        mastodon_api_base_url="https://mastodon.social",
    )

    await main.capture_follower_snapshot_stage(broadcast, creds)

    rows = saved["data"]["snapshots"]
    assert len(rows) == 1
    assert rows[0]["platform"] == "mastodon"
    assert rows[0]["followers"] == 12
