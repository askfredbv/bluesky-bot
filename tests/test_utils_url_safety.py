import socket

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


def test_is_safe_public_url_rejects_cgnat_shared_space(monkeypatch):
    def mock_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)
    assert is_safe_public_url("https://example.com/article") is False
