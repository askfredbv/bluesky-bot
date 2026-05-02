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
async def test_content_prep_stage_degrades_mode_when_news_is_low(monkeypatch):
    creds = SimpleNamespace(bluesky_username="u", bluesky_password="p")

    class FakeClient:
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
        return ["hello"], "topic"

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

    await main.capture_post_metrics_stage(broadcast)

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
async def test_capture_post_metrics_stage_is_noop_when_nothing_sent(monkeypatch):
    """Empty sent_uris on both platforms should not call save."""
    save_calls = []
    monkeypatch.setattr("src.metrics.load_post_metrics", lambda: {"posts": []})
    monkeypatch.setattr("src.metrics.save_post_metrics", lambda d: save_calls.append(d))

    broadcast = main.BroadcastPayload(
        mode="mentor",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        content_list=["unused"],
        chosen_topic="topic",
        thread_pause_profile="default",
        bsky_broadcast_client=None,
    )
    await main.capture_post_metrics_stage(broadcast)

    assert save_calls == []
