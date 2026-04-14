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
        gemini_api_key="gemini-key",
        mastodon_access_token="mastodon-token",
        mastodon_api_base_url="https://mastodon.example",
    )
    settings = SimpleNamespace(
        credentials=creds,
        platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0),
    )

    class FailingAsyncClient:
        async def login(self, username, password):
            raise RuntimeError("login failed")

    async def fake_generate_content(api_key, recent_posts, mode, news_items):
        calls["recent_posts"] = list(recent_posts)
        return ["thread post"], "topic"

    async def fake_post_to_bluesky(*args, **kwargs):
        calls["bluesky"] += 1
        raise RuntimeError("bsky broadcast fails")

    async def fake_post_to_mastodon(*args, **kwargs):
        calls["mastodon"] += 1
        return {"status": "ok"}

    async def fake_fetch_news(*args, **kwargs):
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
    monkeypatch.setattr(main, "handle_interactions", noop)
    monkeypatch.setattr(main, "should_update_profile_bio", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "update_seen_articles", lambda *_: None)

    await main.main()

    assert calls["recent_posts"] == []
    assert calls["bluesky"] == 1
    assert calls["mastodon"] == 1


@pytest.mark.asyncio
async def test_bluesky_preflight_failure_uses_recent_posts_fallback_and_logs_warning(monkeypatch):
    calls = {"recent_posts": None, "warn": []}

    creds = SimpleNamespace(
        bluesky_username="bsky-user",
        bluesky_password="bsky-pass",
        gemini_api_key="gemini-key",
        mastodon_access_token="mastodon-token",
        mastodon_api_base_url="https://mastodon.example",
    )
    settings = SimpleNamespace(
        credentials=creds,
        platform=SimpleNamespace(post_jitter_min_seconds=0, post_jitter_max_seconds=0),
    )

    class FailingAsyncClient:
        async def login(self, username, password):
            raise ValueError("bad credentials")

    async def fake_generate_content(api_key, recent_posts, mode, news_items):
        calls["recent_posts"] = list(recent_posts)
        return ["thread post"], "topic"

    async def fake_fetch_news(*args, **kwargs):
        return []

    async def noop(*args, **kwargs):
        return None

    def capture_warn(event, message="", **fields):
        calls["warn"].append((event, message, fields))

    monkeypatch.setitem(sys.modules, "atproto", types.SimpleNamespace(AsyncClient=FailingAsyncClient))
    monkeypatch.setattr(main, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(main, "load_seen_articles", lambda: {"links": [], "recent_topics": []})
    monkeypatch.setattr(main, "fetch_news", fake_fetch_news)
    monkeypatch.setattr(main, "get_link_metadata", noop)
    monkeypatch.setattr(main, "generate_content", fake_generate_content)
    monkeypatch.setattr(main, "post_to_bluesky", noop)
    monkeypatch.setattr(main, "post_to_mastodon", noop)
    monkeypatch.setattr(main, "handle_interactions", noop)
    monkeypatch.setattr(main, "should_update_profile_bio", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "update_seen_articles", lambda *_: None)
    monkeypatch.setattr(main.SafeLogger, "warn", capture_warn)

    await main.main()

    assert calls["recent_posts"] == []
    matching = [entry for entry in calls["warn"] if entry[0] == "bluesky_preflight_failed"]
    assert matching
    _, _, fields = matching[0]
    assert fields["platform"] == "bluesky"
    assert fields["error_type"] == "ValueError"
    assert fields["fallback_recent_posts_count"] == 0
