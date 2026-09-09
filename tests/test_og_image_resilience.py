"""A bad OpenGraph thumbnail must not cost the card its headline.

get_link_metadata wrapped the whole scrape -- parse AND image fetch -- in one
try/except that returns the generic "Source Link" fallback. The thumbnail is an
enrichment; the title and description are the payload. Any failure downloading or
re-encoding the picture used to discard both, so an article whose OpenGraph tags
parsed perfectly well shipped a link card titled "Source Link".
"""

import io

import pytest
from PIL import Image

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


def _article_html() -> str:
    return (
        '<html><head>'
        '<meta property="og:title" content="The Real Headline"/>'
        '<meta property="og:description" content="The real description."/>'
        '<meta property="og:image" content="https://cdn.example.com/hero/story-123.jpg"/>'
        '</head></html>'
    )


def _truncated_png() -> bytes:
    """A PNG whose header parses but whose pixel data is cut short.

    Pillow decodes lazily: Image.open reads the header and succeeds, and the
    failure surfaces later at convert()/save(). That asymmetry is exactly what
    compress_image's original guard missed.
    """
    buf = io.BytesIO()
    Image.new("RGBA", (64, 64), (200, 30, 30, 255)).save(buf, format="PNG")
    raw = buf.getvalue()
    return raw[: len(raw) // 2]


def _patch_url_guards(monkeypatch):
    monkeypatch.setattr(utils, "is_safe_public_url", lambda _: True)
    monkeypatch.setattr(utils, "is_allowed_metadata_fetch_url", lambda _: True)
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda *a, **kw: _FakeClient())


def test_compress_image_returns_original_bytes_on_a_truncated_file():
    """compress_image must never raise -- is_usable_image's docstring, and every
    caller, rely on 'returns the ORIGINAL bytes when Pillow cannot open or shrink
    them'. The guard used to cover only Image.open, so a lazy-decode failure at
    convert()/save() escaped instead."""
    broken = _truncated_png()

    result = utils.compress_image(broken)

    assert result == broken  # original bytes back, no exception


def test_compress_image_still_shrinks_a_valid_image():
    """The happy path is unchanged: a real image is re-encoded, not passed through."""
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (10, 120, 200)).save(buf, format="PNG")
    original = buf.getvalue()

    result = utils.compress_image(original)

    assert result != original
    assert utils.is_usable_image(result)


@pytest.mark.asyncio
async def test_metadata_survives_a_failed_thumbnail_fetch(monkeypatch):
    """A dropped connection on the image costs the picture, not the headline."""
    _patch_url_guards(monkeypatch)

    async def fake_safe_redirects(client, url, **kwargs):
        if "article" in url:
            return _Resp(text=_article_html())
        raise ConnectionError("connection reset by peer")

    monkeypatch.setattr(utils, "get_with_safe_redirects", fake_safe_redirects)

    meta = await utils.get_link_metadata("https://example.com/article")

    assert meta["title"] == "The Real Headline"
    assert meta["description"] == "The real description."
    assert meta["image_data"] is None
    assert meta["url"] == "https://example.com/article"


@pytest.mark.asyncio
async def test_metadata_survives_a_failed_compression(monkeypatch):
    """Defence in depth: compress_image no longer raises, but get_link_metadata
    must not depend on that to keep the article's own metadata."""
    _patch_url_guards(monkeypatch)

    async def fake_safe_redirects(client, url, **kwargs):
        if "article" in url:
            return _Resp(text=_article_html())
        return _Resp(content=b"x" * (901 * 1024))  # over the compression threshold

    def boom(*_a, **_k):
        raise OSError("image file is truncated")

    monkeypatch.setattr(utils, "get_with_safe_redirects", fake_safe_redirects)
    monkeypatch.setattr(utils, "compress_image", boom)

    meta = await utils.get_link_metadata("https://example.com/article")

    assert meta["title"] == "The Real Headline"
    assert meta["description"] == "The real description."
    assert meta["image_data"] is None


@pytest.mark.asyncio
async def test_a_genuinely_unreachable_article_still_falls_back(monkeypatch):
    """The fallback is still correct when the ARTICLE itself fails -- this change
    narrows what counts as a metadata failure, it does not remove the handler."""
    _patch_url_guards(monkeypatch)

    async def fake_safe_redirects(client, url, **kwargs):
        raise ConnectionError("host unreachable")

    monkeypatch.setattr(utils, "get_with_safe_redirects", fake_safe_redirects)

    meta = await utils.get_link_metadata("https://example.com/article")

    assert meta["title"] == "Source Link"
    assert meta["image_data"] is None
