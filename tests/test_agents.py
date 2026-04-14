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
