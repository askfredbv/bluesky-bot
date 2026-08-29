import socket
from urllib.parse import urlparse

import httpx
import pytest

from src import net_safety
from src.utils import get_with_safe_redirects
from src.utils import is_safe_public_url


def test_is_safe_public_url_rejects_non_http_scheme():
    assert is_safe_public_url("ftp://example.com/file.txt") is False


def test_is_safe_public_url_rejects_localhost():
    assert is_safe_public_url("http://localhost:8080/health") is False


def test_is_safe_public_url_rejects_private_ip(monkeypatch):
    def mock_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    assert is_safe_public_url("https://example.com/article") is False


def test_is_safe_public_url_accepts_public_ip(monkeypatch):
    def mock_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    assert is_safe_public_url("https://example.com/article") is True


@pytest.mark.asyncio
async def test_get_with_safe_redirects_blocks_dns_rebinding_on_same_hop(monkeypatch):
    call_count = {"count": 0}

    def mock_getaddrinfo(host, *args, **kwargs):
        call_count["count"] += 1
        if call_count["count"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    async def fake_capped(client, url, **kwargs):
        host = urlparse(url).hostname
        socket.getaddrinfo(host, None)
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(net_safety, "_capped_stream_get", fake_capped)
        response = await get_with_safe_redirects(client, "https://example.com/path")

    assert response is None


@pytest.mark.asyncio
async def test_get_with_safe_redirects_validates_each_redirect_hop(monkeypatch):
    def mock_getaddrinfo(host, *args, **kwargs):
        if host == "first.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        if host == "second.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]
        raise socket.gaierror("unknown host")

    requests_made = []

    async def fake_capped(client, url, **kwargs):
        requests_made.append(url)
        if url == "https://first.example/start":
            return httpx.Response(
                302,
                headers={"location": "https://second.example/final"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(net_safety, "_capped_stream_get", fake_capped)
        response = await get_with_safe_redirects(client, "https://first.example/start")

    assert response is None
    assert requests_made == ["https://first.example/start"]


@pytest.mark.asyncio
async def test_get_with_safe_redirects_allows_allowlisted_domain(monkeypatch):
    def mock_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    async def fake_capped(client, url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_ALLOWED_DOMAINS", ["allowed.example"])
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_BLOCKED_DOMAINS", [])

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(net_safety, "_capped_stream_get", fake_capped)
        response = await get_with_safe_redirects(client, "https://allowed.example/article")

    assert response is not None
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_with_safe_redirects_blocks_non_allowlisted_domain(monkeypatch):
    def mock_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    async def fake_capped(client, url, **kwargs):
        raise AssertionError("request should not execute when domain policy blocks")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_ALLOWED_DOMAINS", ["allowed.example"])
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_BLOCKED_DOMAINS", [])

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(net_safety, "_capped_stream_get", fake_capped)
        response = await get_with_safe_redirects(client, "https://disallowed.example/article")

    assert response is None


@pytest.mark.asyncio
async def test_get_with_safe_redirects_blocks_redirect_to_non_allowlisted_domain(monkeypatch):
    def mock_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    requests_made = []

    async def fake_capped(client, url, **kwargs):
        requests_made.append(url)
        if url == "https://allowed.example/start":
            return httpx.Response(
                302,
                headers={"location": "https://disallowed.example/final"},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_ALLOWED_DOMAINS", ["allowed.example"])
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_BLOCKED_DOMAINS", [])

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(net_safety, "_capped_stream_get", fake_capped)
        response = await get_with_safe_redirects(client, "https://allowed.example/start")

    assert response is None
    assert requests_made == ["https://allowed.example/start"]


def test_pillow_decompression_bomb_cap_is_set():
    """The process-wide Pillow pixel cap must stay in place — it guards every
    Image.open against a malicious feed serving a decompression-bomb thumbnail."""
    from PIL import Image

    import src.utils  # noqa: F401 — importing sets the cap as a side effect

    assert Image.MAX_IMAGE_PIXELS == 10_000_000


def test_compress_image_survives_decompression_bomb(monkeypatch):
    """A bomb (or any Pillow decode error) must degrade to the original bytes,
    never crash the run."""
    from PIL import Image

    from src.utils import compress_image

    def _boom(*_a, **_kw):
        raise Image.DecompressionBombError("image is too large")

    monkeypatch.setattr("src.utils.Image.open", _boom)
    original = b"not-a-real-image-but-that-is-fine"
    assert compress_image(original) == original


# --- response size cap (_capped_stream_get) -------------------------------

class _FakeStreamResponse:
    def __init__(self, chunks, *, is_redirect=False, headers=None, status_code=200):
        self.is_redirect = is_redirect
        self.headers = headers or {}
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://example.com/x")
        self._chunks = chunks

    async def aiter_raw(self):
        for c in self._chunks:
            yield c


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


def _client_streaming(resp):
    class _C:
        def stream(self, method, url, **kwargs):
            return _FakeStreamCtx(resp)
    return _C()


@pytest.mark.asyncio
async def test_capped_stream_get_aborts_on_streamed_oversize():
    resp = _FakeStreamResponse([b"x" * 1000, b"x" * 1000, b"x" * 1000])  # 3000 B
    out = await net_safety._capped_stream_get(
        _client_streaming(resp), "https://example.com/x",
        headers=None, timeout=1.0, max_bytes=1500)
    assert out is None  # streamed past the cap


@pytest.mark.asyncio
async def test_capped_stream_get_rejects_declared_oversize():
    resp = _FakeStreamResponse([b"x"], headers={"content-length": "9999999"})
    out = await net_safety._capped_stream_get(
        _client_streaming(resp), "https://example.com/x",
        headers=None, timeout=1.0, max_bytes=1500)
    assert out is None  # declared Content-Length over the cap


@pytest.mark.asyncio
async def test_capped_stream_get_returns_body_under_cap():
    resp = _FakeStreamResponse([b"hello ", b"world"])
    out = await net_safety._capped_stream_get(
        _client_streaming(resp), "https://example.com/x",
        headers=None, timeout=1.0, max_bytes=1500)
    assert out is not None and out.content == b"hello world"


@pytest.mark.asyncio
async def test_capped_stream_get_decodes_gzip_under_cap():
    import gzip
    payload = b"hello gzip world"
    resp = _FakeStreamResponse([gzip.compress(payload)],
                               headers={"content-encoding": "gzip"})
    out = await net_safety._capped_stream_get(
        _client_streaming(resp), "https://example.com/x",
        headers=None, timeout=1.0, max_bytes=1500)
    assert out is not None and out.content == payload
    assert "content-encoding" not in out.headers  # decoded, header stripped


@pytest.mark.asyncio
async def test_capped_stream_get_blocks_gzip_bomb():
    import gzip
    bomb = gzip.compress(b"x" * 5_000_000)  # tiny compressed, 5 MB decompressed
    assert len(bomb) < 50_000  # confirm the payload itself is small
    resp = _FakeStreamResponse([bomb], headers={"content-encoding": "gzip"})
    out = await net_safety._capped_stream_get(
        _client_streaming(resp), "https://example.com/x",
        headers=None, timeout=1.0, max_bytes=100_000)
    assert out is None  # decompressed size exceeds the cap -> refused


@pytest.mark.asyncio
async def test_capped_stream_get_refuses_unbounded_encoding():
    resp = _FakeStreamResponse([b"whatever"], headers={"content-encoding": "br"})
    out = await net_safety._capped_stream_get(
        _client_streaming(resp), "https://example.com/x",
        headers=None, timeout=1.0, max_bytes=1500)
    assert out is None  # br is not bounded here -> fail safe


@pytest.mark.asyncio
async def test_capped_stream_get_handles_malformed_gzip():
    resp = _FakeStreamResponse([b"not-actually-gzip"],
                               headers={"content-encoding": "gzip"})
    out = await net_safety._capped_stream_get(
        _client_streaming(resp), "https://example.com/x",
        headers=None, timeout=1.0, max_bytes=1500)
    assert out is None  # malformed gzip -> refused (logged as gzip_decode_failed)
