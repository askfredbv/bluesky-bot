import sys
import types
from types import SimpleNamespace

import pytest

import main


@pytest.mark.asyncio
async def test_bluesky_preflight_failure_still_runs_downstream_posting(monkeypatch):
    calls = {"bluesky": 0, "mastodon": 0, "recent_posts": None}

    creds = SimpleNamespace(
        bluesky_username="bsky-user",
        bluesky_password="bsky-pass",
        gemini_api_key="gemini-key", anthropic_api_key=None,
        mastodon_access_token="mastodon-token",
        mastodon_api_base_url="https://mastodon.example",
    )
    settings = SimpleNamespace(
        credentials=creds,
        platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0),
    )

    class FailingAsyncClient:
        def on_session_change(self, cb): return cb
        async def login(self, username, password):
            raise RuntimeError("login failed")

    async def fake_generate_content(api_key, recent_posts, mode, news_items, **kwargs):
        calls["recent_posts"] = list(recent_posts)
        return ["thread post"], "topic", None

    async def fake_post_to_bluesky(*args, **kwargs):
        calls["bluesky"] += 1
        from src.metrics import BroadcastResult
        return BroadcastResult(client="bsky-client", sent_uris=["at://fake"])

    async def fake_post_to_mastodon(*args, **kwargs):
        calls["mastodon"] += 1
        from src.metrics import BroadcastResult
        return BroadcastResult(client=None, sent_uris=["123"])

    async def fake_fetch_news(*args, **kwargs):
        return []

    async def fake_recent_posts(*args, **kwargs):
        return []

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setitem(sys.modules, "atproto", types.SimpleNamespace(AsyncClient=FailingAsyncClient))
    monkeypatch.setattr(main, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(main, "load_seen_articles", lambda: {"links": [], "recent_topics": []})
    monkeypatch.setattr(main, "fetch_news", fake_fetch_news)
    monkeypatch.setattr(main, "get_link_metadata", noop)
    monkeypatch.setattr(main, "generate_content", fake_generate_content)
    monkeypatch.setattr(main, "post_to_bluesky", fake_post_to_bluesky)
    monkeypatch.setattr(main, "post_to_mastodon", fake_post_to_mastodon)
    monkeypatch.setattr(main, "update_seen_articles", lambda *_: None)
    monkeypatch.setattr(main, "capture_post_metrics_stage", noop)
    monkeypatch.setattr(main, "capture_follower_snapshot_stage", noop)

    # Bluesky got nothing, so the run now ends red (#293); the rest still ran.
    with pytest.raises(SystemExit):
        await main.main()

    assert calls["recent_posts"] == []
    # Both logins fail (FailingAsyncClient) → Bluesky is skipped, not attempted
    assert calls["bluesky"] == 0
    assert calls["mastodon"] == 1


@pytest.mark.asyncio
async def test_bluesky_preflight_failure_uses_recent_posts_fallback_and_logs_error(monkeypatch):
    calls = {"recent_posts": None, "error": []}

    creds = SimpleNamespace(
        bluesky_username="bsky-user",
        bluesky_password="bsky-pass",
        gemini_api_key="gemini-key", anthropic_api_key=None,
        mastodon_access_token="mastodon-token",
        mastodon_api_base_url="https://mastodon.example",
    )
    settings = SimpleNamespace(
        credentials=creds,
        platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0),
    )

    class FailingAsyncClient:
        def on_session_change(self, cb): return cb
        async def login(self, username, password):
            raise ValueError("bad credentials")

    async def fake_generate_content(api_key, recent_posts, mode, news_items, **kwargs):
        calls["recent_posts"] = list(recent_posts)
        return ["thread post"], "topic", None

    async def fake_fetch_news(*args, **kwargs):
        return []

    async def noop(*args, **kwargs):
        return None

    def capture_error(event, message="", **fields):
        calls["error"].append((event, message, fields))

    monkeypatch.setitem(sys.modules, "atproto", types.SimpleNamespace(AsyncClient=FailingAsyncClient))
    monkeypatch.setattr(main, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(main, "load_seen_articles", lambda: {"links": [], "recent_topics": []})
    monkeypatch.setattr(main, "fetch_news", fake_fetch_news)
    monkeypatch.setattr(main, "get_link_metadata", noop)
    monkeypatch.setattr(main, "generate_content", fake_generate_content)
    monkeypatch.setattr(main, "post_to_bluesky", noop)
    monkeypatch.setattr(main, "post_to_mastodon", noop)
    monkeypatch.setattr(main, "update_seen_articles", lambda *_: None)
    monkeypatch.setattr(main, "capture_post_metrics_stage", noop)
    monkeypatch.setattr(main, "capture_follower_snapshot_stage", noop)
    monkeypatch.setattr(main.SafeLogger, "error", capture_error)

    # Bluesky got nothing, so the run now ends red (#293); the rest still ran.
    with pytest.raises(SystemExit):
        await main.main()

    assert calls["recent_posts"] == []
    matching = [entry for entry in calls["error"] if entry[0] == "bluesky_preflight_failed"]
    assert matching
    _, _, fields = matching[0]
    assert fields["platform"] == "bluesky"
    assert fields["error_type"] == "ValueError"
    assert fields["fallback_recent_posts_count"] == 0


@pytest.mark.asyncio
async def test_curator_switches_to_strategist_when_feed_volume_is_low(monkeypatch):
    calls = {"warn": []}
    creds = SimpleNamespace(bluesky_username="bsky-user", bluesky_password="bsky-pass")

    class FakeAsyncClient:
        def on_session_change(self, cb): return cb
        async def login(self, username, password):
            return None

    async def fake_fetch_news(*args, **kwargs):
        return [{"link": "https://example.com/one"}, {"link": "https://example.com/two"}]

    async def fake_recent_posts(*args, **kwargs):
        return ["already posted"]

    def capture_warn(event, message="", **fields):
        calls["warn"].append((event, fields))

    monkeypatch.setattr(main, "load_seen_articles", lambda: {"links": [], "recent_topics": []})
    monkeypatch.setattr(main, "fetch_news", fake_fetch_news)
    monkeypatch.setattr(main, "get_recent_posts", fake_recent_posts)
    monkeypatch.setitem(sys.modules, "atproto", types.SimpleNamespace(AsyncClient=FakeAsyncClient))
    monkeypatch.setattr(main.SafeLogger, "warn", capture_warn)
    # Pinned: the Pioneer roll is random, and when it misses the Strategist
    # writes from the two items, which would fetch their link card.
    monkeypatch.setattr(main, "select_pioneer_topic", lambda *_: {"entry": {"id": "x"}, "pool": "undated"})

    payload = await main.content_prep_stage(main.ModeSelectionPayload(mode="curator", current_hour_utc=8), creds)

    assert payload.mode == "strategist"
    assert payload.news_items == []  # Pioneer wins over the thin news
    assert payload.link_meta is None
    assert payload.recent_posts == ["already posted"]
    assert any(event == "news_volume_low" for event, _ in calls["warn"])


@pytest.mark.asyncio
async def test_partial_broadcast_failure_keeps_successful_bluesky_client(monkeypatch):
    calls = {"errors": []}
    creds = SimpleNamespace(
        gemini_api_key="gemini-key", anthropic_api_key=None,
        bluesky_username="bsky-user",
        bluesky_password="bsky-pass",
        mastodon_access_token="mastodon-token",
        mastodon_api_base_url="https://mastodon.example",
    )
    settings = SimpleNamespace(platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0))

    async def fake_generate_content(*args, **kwargs):
        return ["thread post"], "topic", None

    async def ok_bluesky(*args, **kwargs):
        from src.metrics import BroadcastResult
        return BroadcastResult(client="live-bluesky-client", sent_uris=["at://post"])

    async def failing_mastodon(*args, **kwargs):
        raise RuntimeError("mastodon down")

    async def no_delay(*args, **kwargs):
        return None

    def capture_error(event, message="", exception=None, **fields):
        calls["errors"].append((event, type(exception).__name__ if exception else None, fields))

    monkeypatch.setattr(main, "generate_content", fake_generate_content)
    monkeypatch.setattr(main, "post_to_bluesky", ok_bluesky)
    monkeypatch.setattr(main, "post_to_mastodon", failing_mastodon)
    monkeypatch.setattr(main, "apply_humanized_post_delay", no_delay)
    monkeypatch.setattr(main.random, "choice", lambda seq: "default")
    monkeypatch.setattr(main.SafeLogger, "error", capture_error)

    prep = main.ContentPrepPayload(
        mode="mentor",
        seen_data={"links": [], "recent_topics": []},
        news_items=[],
        link_meta=None,
        bsky_client="fallback-client",
        recent_posts=[],
    )
    payload = await main.broadcasting_stage(prep, settings, creds)

    assert payload.bsky_broadcast_client == "live-bluesky-client"
    assert any(event == "broadcast_task_failed" and err_type == "RuntimeError" for event, err_type, _ in calls["errors"])




def _payload(content, bsky, masto):
    return main.BroadcastPayload(
        mode="curator", seen_data={}, news_items=[], content_list=content,
        chosen_topic="t", thread_pause_profile="default", bsky_broadcast_client=None,
        bsky_sent_uris=bsky, mastodon_sent_ids=masto,
    )


@pytest.mark.parametrize(
    ("content", "bsky", "masto", "mastodon_enabled", "expected"),
    [
        (["a"], ["at://1"], ["1"], True, []),                    # both delivered
        (["a"], ["at://1"], [], True, ["mastodon"]),             # 2026-11-18: Mastodon 404
        (["a", "b"], ["at://1", "at://2"], ["1"], True, ["mastodon"]),  # partial thread
        (["a"], [], ["1"], True, ["bluesky"]),                   # Bluesky login failed
        (["a"], [], [], True, ["bluesky", "mastodon"]),
        (["a"], ["at://1"], [], False, []),                      # Mastodon not configured
        ([], [], [], True, []),                                  # skipped run stays green
    ],
)
def test_incomplete_platforms(content, bsky, masto, mastodon_enabled, expected):
    assert main.incomplete_platforms(_payload(content, bsky, masto), mastodon_enabled) == expected


@pytest.mark.asyncio
async def test_main_exits_red_after_persisting_when_a_platform_misses(monkeypatch):
    order = []
    creds = SimpleNamespace(mastodon_access_token="tok", gemini_api_key="k", anthropic_api_key=None)
    monkeypatch.setattr(main, "load_settings_or_exit", lambda: SimpleNamespace(credentials=creds))

    async def fake_filter(*a, **k):
        return ["m"]

    async def fake_mode():
        return SimpleNamespace(mode="curator")

    async def fake_prep(*a, **k):
        return None

    async def fake_broadcast(*a, **k):
        return _payload(["a"], ["at://1"], [])

    async def fake_persist(*a, **k):
        order.append("persisted")

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(main, "filter_available_models", fake_filter)
    monkeypatch.setattr(main, "mode_selection_stage", fake_mode)
    monkeypatch.setattr(main, "content_prep_stage", fake_prep)
    monkeypatch.setattr(main, "broadcasting_stage", fake_broadcast)
    monkeypatch.setattr(main, "build_automation_payload", lambda b: b)
    monkeypatch.setattr(main, "persistence_stage", fake_persist)
    monkeypatch.setattr(main, "capture_post_metrics_stage", noop)
    monkeypatch.setattr(main, "capture_follower_snapshot_stage", noop)
    monkeypatch.setattr(main.SafeLogger, "error", lambda event, *a, **f: order.append(event))

    with pytest.raises(SystemExit) as exc:
        await main.main()

    assert exc.value.code == 1
    assert order == ["persisted", "run_delivery_incomplete"]
