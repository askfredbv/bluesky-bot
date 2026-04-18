"""Tests for the generic-logo filter in get_link_metadata.

When the scraped og:image URL matches a known "useless thumbnail" pattern
(org logos, default share images), we skip the image fetch entirely and
return a link card with no thumb rather than attaching a cluttered logo.
"""

import pytest

from src import utils


class _Resp:
    def __init__(self, text: str = "", content: bytes = b"", status_code: int = 200):
        self.text = text
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        return None


class _FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _html_with_og_image(img_url: str) -> str:
    return (
        f'<html><head>'
        f'<meta property="og:title" content="An Article"/>'
        f'<meta property="og:image" content="{img_url}"/>'
        f'</head></html>'
    )


@pytest.mark.asyncio
async def test_generic_logo_thumbnail_is_skipped(monkeypatch):
    """An og:image matching GENERIC_IMAGE_PATTERNS should not be fetched."""
    image_fetched = {"called": False}

    async def fake_safe_redirects(client, url, **kwargs):
        if "article" in url:
            return _Resp(text=_html_with_og_image("https://cdn.example.com/arxiv-logo.png"))
        image_fetched["called"] = True
        return _Resp(content=b"should-not-fetch")

    monkeypatch.setattr(utils, "is_safe_public_url", lambda _: True)
    monkeypatch.setattr(utils, "is_allowed_metadata_fetch_url", lambda _: True)
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())
    monkeypatch.setattr(utils, "get_with_safe_redirects", fake_safe_redirects)

    meta = await utils.get_link_metadata("https://example.com/article")

    assert meta["title"] == "An Article"
    assert meta["image_data"] is None
    assert image_fetched["called"] is False, "Generic logo URL should not have been fetched"


@pytest.mark.asyncio
async def test_non_generic_image_is_fetched_normally(monkeypatch):
    """A real-looking image URL should be fetched as usual."""
    image_fetched = {"called": False}

    async def fake_safe_redirects(client, url, **kwargs):
        if "article" in url:
            return _Resp(text=_html_with_og_image("https://cdn.example.com/hero/story-123.jpg"))
        image_fetched["called"] = True
        return _Resp(content=b"real-image-bytes", status_code=200)

    monkeypatch.setattr(utils, "is_safe_public_url", lambda _: True)
    monkeypatch.setattr(utils, "is_allowed_metadata_fetch_url", lambda _: True)
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())
    monkeypatch.setattr(utils, "get_with_safe_redirects", fake_safe_redirects)

    meta = await utils.get_link_metadata("https://example.com/article")

    assert meta["image_data"] == b"real-image-bytes"
    assert image_fetched["called"] is True


@pytest.mark.asyncio
async def test_favicon_is_skipped(monkeypatch):
    """The 'favicon' pattern is in GENERIC_IMAGE_PATTERNS."""
    image_fetched = {"called": False}

    async def fake_safe_redirects(client, url, **kwargs):
        if "article" in url:
            return _Resp(text=_html_with_og_image("https://example.com/favicon.ico"))
        image_fetched["called"] = True
        return _Resp(content=b"x")

    monkeypatch.setattr(utils, "is_safe_public_url", lambda _: True)
    monkeypatch.setattr(utils, "is_allowed_metadata_fetch_url", lambda _: True)
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())
    monkeypatch.setattr(utils, "get_with_safe_redirects", fake_safe_redirects)

    meta = await utils.get_link_metadata("https://example.com/article")

    assert meta["image_data"] is None
    assert image_fetched["called"] is False
