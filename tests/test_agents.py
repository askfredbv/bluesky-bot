import pytest

from src.agents import generate_content, handle_interactions, _truncate_for_platform, _sync_generate, generate_post_image
from src import agents
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

    def _capture_prompt(_api_key, prompt, _model):
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

    def _capture_prompt(_api_key, prompt, _model):
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


def test_sync_generate_reuses_cached_client(monkeypatch):
    call_count = {"count": 0}
    test_cache = {}

    class DummyResponse:
        text = "ok"

    class DummyModels:
        def generate_content(self, model, contents):
            return DummyResponse()

    class DummyClient:
        def __init__(self, api_key):
            call_count["count"] += 1
            self.models = DummyModels()

    monkeypatch.setattr("src.agents._CLIENT_CACHE", test_cache)
    monkeypatch.setattr("src.agents.genai.Client", DummyClient)

    assert _sync_generate("fake-key", "prompt 1", "gemini-2.5-flash") == "ok"
    assert _sync_generate("fake-key", "prompt 2", "gemini-2.5-flash") == "ok"
    assert call_count["count"] == 1


def test_sync_generate_missing_key_raises_clear_error():
    with pytest.raises(ValueError, match="missing or empty"):
        _sync_generate("", "prompt", "gemini-2.5-flash")


def test_sync_generate_invalid_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr("src.agents._CLIENT_CACHE", {})

    def _raise_client_error(*_args, **_kwargs):
        raise RuntimeError("bad credentials")

    monkeypatch.setattr("src.agents.genai.Client", _raise_client_error)

    with pytest.raises(ValueError, match="Failed to initialize Gemini client"):
        _sync_generate("invalid-key", "prompt", "gemini-2.5-flash")


@pytest.mark.asyncio
async def test_model_failover_advances_to_next_model_on_api_error(monkeypatch):
    """An API-level exception on the first model should cause the next model to be tried."""
    models_tried = []

    def _track_and_fail_first(api_key, prompt, model):
        models_tried.append(model)
        if model == "gemini-2.5-flash":
            raise ConnectionError("quota exceeded")
        return '["This is a long enough post that covers #AI and passes all validation checks."]'

    monkeypatch.setattr("src.agents._sync_generate", _track_and_fail_first)

    content, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
    )

    assert "gemini-2.5-flash" in models_tried
    assert "gemini-2.0-flash" in models_tried
    assert isinstance(content, list)
    assert len(content) >= 1
    assert "quota exceeded" not in content[0]


@pytest.mark.asyncio
async def test_model_failover_does_not_advance_on_json_error(monkeypatch):
    """A JSON decode error is a content quality issue; the same model should be retried."""
    models_tried = []
    attempt_count = [0]

    def _track_and_return_bad_json(api_key, prompt, model):
        models_tried.append(model)
        attempt_count[0] += 1
        return "not valid json at all"

    monkeypatch.setattr("src.agents._sync_generate", _track_and_return_bad_json)

    content, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
    )

    # Should have retried the same models 2x each — never jumped early due to JSON error
    # All models will be tried (2 attempts each), ending in fallback
    assert models_tried.count("gemini-2.5-flash") == 2
    assert isinstance(content, list)
    assert len(content) == 1  # fallback post


@pytest.mark.asyncio
async def test_model_used_event_logged_on_success(monkeypatch):
    """A successful generation should log the model_used event."""
    log_events = []

    def _ok_generate(api_key, prompt, model):
        return '["Long enough post with #AI and enough detail to pass all validation checks here."]'

    def _capture_info(event, message="", **fields):
        log_events.append((event, fields))

    monkeypatch.setattr("src.agents._sync_generate", _ok_generate)
    monkeypatch.setattr("src.agents.SafeLogger.info", _capture_info)

    await generate_content(api_key="fake-key", recent_posts=[], mode="mentor")

    assert any(event == "model_used" for event, _ in log_events)
    model_event = next((fields for event, fields in log_events if event == "model_used"), None)
    assert model_event is not None
    assert "model" in model_event


@pytest.mark.asyncio
async def test_generate_content_injects_language_directive(monkeypatch):
    """LANGUAGE directive is present in the prompt sent to Gemini."""
    captured = {}

    def fake_sync_generate(api_key, prompt, model):
        captured["prompt"] = prompt
        return '["Long enough post with #AI and enough detail to pass all validation checks here."]'

    monkeypatch.setattr("src.agents._sync_generate", fake_sync_generate)

    await generate_content(api_key="fake-key", recent_posts=[], mode="mentor")

    assert "LANGUAGE:" in captured["prompt"]
    assert any(lang in captured["prompt"] for lang in ["English", "Dutch"])


@pytest.mark.asyncio
async def test_generate_post_image_returns_bytes_on_success(monkeypatch):
    """generate_post_image returns raw bytes when _sync_generate_image succeeds."""
    monkeypatch.setattr(agents, "_sync_generate_image", lambda k, p: b"fake-image-bytes")
    result = await generate_post_image("fake-key", "automation")
    assert result == b"fake-image-bytes"


@pytest.mark.asyncio
async def test_generate_post_image_returns_none_on_failure(monkeypatch):
    """generate_post_image returns None gracefully when image generation fails."""
    def raise_error(k, p):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(agents, "_sync_generate_image", raise_error)
    result = await generate_post_image("fake-key", "automation")
    assert result is None
