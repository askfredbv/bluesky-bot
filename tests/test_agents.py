import pytest

from src.agents import generate_content, handle_interactions, _truncate_for_platform
from src.config import REPLY_MAX_CHARS


@pytest.mark.asyncio
async def test_generate_content_falls_back_when_model_always_fails(monkeypatch):
    """If every model call fails, function should return a safe fallback list and topic."""

    def _always_fail(*args, **kwargs):
        raise RuntimeError("forced generation error")

    monkeypatch.setattr("src.agents._sync_generate", _always_fail)

    content, topic = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="curator",
        news_items=[{"title": "Fallback Topic", "description": "desc", "link": "https://example.com"}],
    )

    assert isinstance(content, list)
    assert content
    assert all(isinstance(item, str) for item in content)
    assert topic == "Fallback Topic"


@pytest.mark.asyncio
async def test_generate_content_includes_style_memory_constraints(monkeypatch):
    captured = {"prompt": ""}

    def _capture_prompt(_api_key, prompt):
        captured["prompt"] = prompt
        return '["This is a long enough primary post with #AI and enough detail to pass validation."]'

    monkeypatch.setattr("src.agents._sync_generate", _capture_prompt)

    await generate_content(
        api_key="fake-key",
        recent_posts=[
            "Automation helps teams ship faster #AI #DevOps",
            "Automation helps teams ship safer #AI #Reliability",
            "Documentation matters for onboarding #DevOps #Docs",
            "Documentation matters for shared context #Docs",
        ],
        mode="mentor",
    )

    assert "RECENT STYLE SIGNALS (AVOID REPETITION)" in captured["prompt"]
    assert "Reused opening patterns" in captured["prompt"]
    assert "Reused hashtags" in captured["prompt"]


@pytest.mark.asyncio
async def test_generate_content_includes_persona_variant(monkeypatch):
    captured = {"prompt": ""}

    def _capture_prompt(_api_key, prompt):
        captured["prompt"] = prompt
        return '["This is a long enough primary post with #AI and enough detail to pass validation."]'

    monkeypatch.setattr("src.agents._sync_generate", _capture_prompt)
    monkeypatch.setattr("src.agents.random.choice", lambda seq: seq[0])

    await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="curator",
        news_items=[{"title": "New Model Launch", "description": "Details", "link": "https://example.com"}],
    )

    assert "PERSONA VARIANT (" in captured["prompt"]


def test_truncate_for_platform_enforces_limit():
    text = "x" * (REPLY_MAX_CHARS + 40)
    assert len(_truncate_for_platform(text, REPLY_MAX_CHARS)) == REPLY_MAX_CHARS


@pytest.mark.asyncio
async def test_handle_interactions_truncates_reply_and_applies_delay(monkeypatch):
    sent_posts = []
    sleep_calls = []
    replied_state = []

    class DummyMention:
        reason = "mention"
        is_read = False
        uri = "at://mention/1"
        cid = "cid-1"

        class record:
            text = "Can you share advice on architecture tradeoffs?"

        class author:
            handle = "alice.example"

    class DummyNotifications:
        notifications = [DummyMention()]

    class DummyNotificationAPI:
        async def list_notifications(self):
            return DummyNotifications()

    class DummyBsky:
        notification = DummyNotificationAPI()

    class DummyApp:
        bsky = DummyBsky()

    class DummyClient:
        app = DummyApp()

        async def send_post(self, text, reply_to):
            sent_posts.append({"text": text, "reply_to": reply_to})

    async def no_sleep(seconds):
        sleep_calls.append(seconds)

    def fake_update_replied_to(mutator):
        nonlocal replied_state
        replied_state = mutator(replied_state)
        return replied_state

    monkeypatch.setattr("src.agents.update_replied_to", fake_update_replied_to)
    monkeypatch.setattr("src.agents.random.random", lambda: 0.95)
    monkeypatch.setattr("src.agents.random.uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr("src.agents.asyncio.sleep", no_sleep)
    monkeypatch.setattr("src.agents._sync_generate", lambda *_args, **_kwargs: "z" * (REPLY_MAX_CHARS + 30))

    await handle_interactions(DummyClient(), "bot.example", "fake-key")

    assert sent_posts
    assert len(sent_posts[0]["text"]) == REPLY_MAX_CHARS
    assert sleep_calls == [0.0]


@pytest.mark.asyncio
async def test_handle_interactions_can_skip_reply_for_human_cadence(monkeypatch):
    sent_posts = []
    replied_state = []

    class DummyMention:
        reason = "mention"
        is_read = False
        uri = "at://mention/skip"
        cid = "cid-skip"

        class record:
            text = "hello"

        class author:
            handle = "bob.example"

    class DummyNotifications:
        notifications = [DummyMention()]

    class DummyNotificationAPI:
        async def list_notifications(self):
            return DummyNotifications()

    class DummyBsky:
        notification = DummyNotificationAPI()

    class DummyApp:
        bsky = DummyBsky()

    class DummyClient:
        app = DummyApp()

        async def send_post(self, text, reply_to):
            sent_posts.append({"text": text, "reply_to": reply_to})

    def fake_update_replied_to(mutator):
        nonlocal replied_state
        replied_state = mutator(replied_state)
        return replied_state

    monkeypatch.setattr("src.agents.update_replied_to", fake_update_replied_to)
    monkeypatch.setattr("src.agents.random.random", lambda: 0.0)

    await handle_interactions(DummyClient(), "bot.example", "fake-key")

    assert sent_posts == []
    assert "at://mention/skip" in replied_state


@pytest.mark.asyncio
async def test_generate_content_falls_back_when_model_returns_non_list(monkeypatch):
    monkeypatch.setattr("src.agents._sync_generate", lambda *_args, **_kwargs: '{"not":"a list"}')

    content, topic = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
    )

    assert isinstance(content, list)
    assert len(content) == 1
