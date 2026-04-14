import pytest

from src.agents import generate_content


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
