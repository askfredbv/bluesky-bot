import socket
from urllib.parse import urlparse

import httpx
import pytest

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

    async def fake_get(url, **kwargs):
        host = urlparse(url).hostname
        socket.getaddrinfo(host, None)
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(client, "get", fake_get)
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

    async def fake_get(url, **kwargs):
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
        monkeypatch.setattr(client, "get", fake_get)
        response = await get_with_safe_redirects(client, "https://first.example/start")

    assert response is None
    assert requests_made == ["https://first.example/start"]


@pytest.mark.asyncio
async def test_get_with_safe_redirects_allows_allowlisted_domain(monkeypatch):
    def mock_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    async def fake_get(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_ALLOWED_DOMAINS", ["allowed.example"])
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_BLOCKED_DOMAINS", [])

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(client, "get", fake_get)
        response = await get_with_safe_redirects(client, "https://allowed.example/article")

    assert response is not None
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_with_safe_redirects_blocks_non_allowlisted_domain(monkeypatch):
    def mock_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    async def fake_get(url, **kwargs):
        raise AssertionError("request should not execute when domain policy blocks")

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_ALLOWED_DOMAINS", ["allowed.example"])
    monkeypatch.setattr("src.net_safety.METADATA_FETCH_BLOCKED_DOMAINS", [])

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(client, "get", fake_get)
        response = await get_with_safe_redirects(client, "https://disallowed.example/article")

    assert response is None


@pytest.mark.asyncio
async def test_get_with_safe_redirects_blocks_redirect_to_non_allowlisted_domain(monkeypatch):
    def mock_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    requests_made = []

    async def fake_get(url, **kwargs):
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
        monkeypatch.setattr(client, "get", fake_get)
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
