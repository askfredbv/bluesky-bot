"""Behaviour of src/net_safety.py (the SSRF guard) that the 2026-09-11 mutation
run found unpinned.

Each section names the lines whose surviving mutants its tests kill. Line
numbers refer to net_safety.py at 49af4a4. Left alone as equivalent or noise:
  - L198, the empty-DNS raise: the "no allowed records" raise below it fires
    anyway;
  - L222 and L334, the MAX_FETCH_BYTES and timeout defaults;
  - L238 and L248, log-only lines;
  - L233, the arithmetic inside the decompression loop: after the first
    capped call either all input is consumed or the output is already over
    the cap, so the loop body never runs. L231 cap + 1 -> cap - 1 does make
    the loop run, and the loop tops the output up to the same bytes. (The bomb
    test pins that no call asks zlib for more than cap + 1 bytes.)
  - L260, gzip wbits 31 -> 32: 32 auto-detects the gzip header and inflates
    the same bytes.
"""
import asyncio
import gzip
import socket
import zlib

import httpx
import pytest

from src import net_safety


def _addrinfo(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


# ---------------------------------------------------------------------------
# Every resolved record must be public (L151-L152)
# ---------------------------------------------------------------------------

def test_a_private_record_behind_a_repeated_public_one_still_rejects_the_host(monkeypatch):
    """With the duplicate-skip `continue` turned into `break`, a repeated public
    record ended the scan and the private record behind it was never checked."""
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: _addrinfo("93.184.216.34", "93.184.216.34", "10.0.0.5"))
    assert net_safety._resolve_public_ip_candidates("example.com") is None


def test_repeated_public_records_are_listed_once(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: _addrinfo("93.184.216.34", "93.184.216.34", "1.1.1.1"))
    assert net_safety._resolve_public_ip_candidates("example.com") == ["93.184.216.34", "1.1.1.1"]


# ---------------------------------------------------------------------------
# URL-shape guards (L108, L115, L125, L162, L168)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["https:///no-host", "http://[::1"])
def test_a_url_without_a_usable_host_is_never_safe(url):
    assert net_safety.is_safe_public_url(url) is False


def test_a_non_ip_string_is_not_a_public_ip():
    assert net_safety._is_public_ip("not-an-ip") is False


def test_an_empty_policy_rule_matches_nothing():
    assert net_safety._hostname_matches_policy("example.com", "") is False
    assert net_safety._hostname_matches_policy("example.com", "  .  ") is False


def test_a_url_without_a_host_is_not_allowed_for_metadata():
    assert net_safety.is_allowed_metadata_fetch_url("https:///x") is False


# ---------------------------------------------------------------------------
# The metadata deny- and allowlist (L172, L178), which no test exercised
# ---------------------------------------------------------------------------

def test_the_allowlist_blocks_every_domain_outside_it(monkeypatch):
    monkeypatch.setattr(net_safety, "METADATA_FETCH_BLOCKED_DOMAINS", [])
    monkeypatch.setattr(net_safety, "METADATA_FETCH_ALLOWED_DOMAINS", ["good.example"])
    assert net_safety.is_allowed_metadata_fetch_url("https://sub.good.example/x") is True
    assert net_safety.is_allowed_metadata_fetch_url("https://other.example/x") is False


@pytest.mark.parametrize("url, allowed", [
    ("https://evil.example/x", False),      # the listed domain
    ("https://cdn.evil.example/x", False),  # and its subdomains
    ("https://notevil.example/x", True),    # a lookalike is a different domain
    ("https://good.example/x", True),
])
def test_the_denylist_blocks_a_domain_and_its_subdomains_only(monkeypatch, url, allowed):
    monkeypatch.setattr(net_safety, "METADATA_FETCH_BLOCKED_DOMAINS", ["evil.example"])
    monkeypatch.setattr(net_safety, "METADATA_FETCH_ALLOWED_DOMAINS", [])
    assert net_safety.is_allowed_metadata_fetch_url(url) is allowed


# ---------------------------------------------------------------------------
# The request itself (L281, L283, L285, L323)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, chunks, *, is_redirect=False, headers=None, status_code=200):
        self.is_redirect = is_redirect
        self.headers = headers or {}
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://example.com/x")
        self._chunks = chunks

    async def aiter_raw(self):
        for chunk in self._chunks:
            yield chunk


class _Ctx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _CapturingClient:
    def __init__(self, resp):
        self.resp = resp
        self.kwargs = None

    def stream(self, method, url, **kwargs):
        self.kwargs = kwargs
        return _Ctx(self.resp)


async def _get(client):
    return await net_safety._capped_stream_get(
        client, "https://example.com/x", headers=None, timeout=1.0, max_bytes=100)


@pytest.mark.asyncio
async def test_httpx_never_follows_redirects_itself_and_identity_is_requested():
    """Redirects must come back to get_with_safe_redirects so each hop is checked:
    httpx following them itself would skip the SSRF guard on every hop after the
    first. Identity encoding keeps the size cap on the real bytes."""
    client = _CapturingClient(_Resp([b"ok"]))
    await _get(client)
    assert client.kwargs["follow_redirects"] is False
    assert client.kwargs["headers"]["Accept-Encoding"] == "identity"


@pytest.mark.asyncio
async def test_a_redirect_comes_back_unread_for_the_caller_to_check():
    resp = _Resp([b"ignored"], is_redirect=True, headers={"location": "https://example.com/next"})
    assert await _get(_CapturingClient(resp)) is resp


@pytest.mark.asyncio
async def test_decoded_gzip_does_not_keep_the_compressed_length():
    body = gzip.compress(b"hello")
    resp = _Resp([body], headers={"content-encoding": "gzip", "content-length": str(len(body))})
    out = await _get(_CapturingClient(resp))
    assert out.content == b"hello"
    assert out.headers.get("content-length") in (None, "5"), "the compressed length survived"


# ---------------------------------------------------------------------------
# Redirect handling (L335, L351, L387, L391, L395, L398, L403)
# ---------------------------------------------------------------------------

def _redirect(url, to):
    return httpx.Response(302, headers={"location": to}, request=httpx.Request("GET", url))


def _ok(url):
    return httpx.Response(200, request=httpx.Request("GET", url))


async def _fetch(monkeypatch, fake, url="https://example.com/start", **kwargs):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34"))
    monkeypatch.setattr(net_safety, "_capped_stream_get", fake)
    async with httpx.AsyncClient() as client:
        return await net_safety.get_with_safe_redirects(
            client, url, **{"enforce_metadata_policy": False, **kwargs})


@pytest.mark.parametrize("enforce", [False, True])
@pytest.mark.asyncio
async def test_a_safe_redirect_is_followed_to_its_final_response(monkeypatch, enforce):
    monkeypatch.setattr(net_safety, "METADATA_FETCH_BLOCKED_DOMAINS", [])
    monkeypatch.setattr(net_safety, "METADATA_FETCH_ALLOWED_DOMAINS", [])
    seen = []

    async def fake(client, url, **k):
        seen.append(url)
        return _redirect(url, "https://example.com/final") if url.endswith("/start") else _ok(url)

    out = await _fetch(monkeypatch, fake, enforce_metadata_policy=enforce)
    assert out is not None and out.status_code == 200
    assert seen == ["https://example.com/start", "https://example.com/final"]


@pytest.mark.asyncio
async def test_a_redirect_with_an_empty_location_is_returned_as_is(monkeypatch):
    async def fake(client, url, **k):
        return _redirect(url, "")

    out = await _fetch(monkeypatch, fake)
    assert out is not None and out.status_code == 302


@pytest.mark.asyncio
async def test_an_https_to_http_redirect_is_refused_before_it_is_requested(monkeypatch):
    seen = []

    async def fake(client, url, **k):
        seen.append(url)
        return _redirect(url, "http://example.com/plain")

    assert await _fetch(monkeypatch, fake) is None
    assert seen == ["https://example.com/start"], "the downgraded URL must never be requested"


@pytest.mark.parametrize("kwargs, requests", [({}, 6), ({"max_redirects": 2}, 3)])
@pytest.mark.asyncio
async def test_a_redirect_loop_stops_after_max_redirects(monkeypatch, kwargs, requests):
    """The first request plus max_redirects hops (5 by default), then it gives up."""
    seen = []

    async def fake(client, url, **k):
        seen.append(url)
        return _redirect(url, f"https://example.com/hop{len(seen)}")

    assert await _fetch(monkeypatch, fake, **kwargs) is None
    assert len(seen) == requests


# ---------------------------------------------------------------------------
# DNS pinning (L46, L193, L207)
# ---------------------------------------------------------------------------

def test_the_pin_lock_still_works_in_a_second_event_loop(monkeypatch):
    """Each asyncio.run() has its own loop. A lock kept from an earlier loop
    raises as soon as two fetches contend for it, and get_with_safe_redirects
    turns that into None: every fetch in the second loop would come back blocked."""
    monkeypatch.setattr(net_safety, "_resolve_public_ip_candidates", lambda host: ["93.184.216.34"])

    async def slow_ok(client, url, **k):
        await asyncio.sleep(0.01)
        return _ok(url)

    monkeypatch.setattr(net_safety, "_capped_stream_get", slow_ok)

    async def two_contending_fetches():
        return await asyncio.gather(*(
            net_safety.get_with_safe_redirects(object(), f"https://example.com/{n}", enforce_metadata_policy=False)
            for n in range(2)))

    for _ in range(2):
        assert all(r is not None for r in asyncio.run(two_contending_fetches()))


def test_the_pin_returns_the_validated_records_and_leaves_other_hosts_alone(monkeypatch):
    records = {"pinned.example": _addrinfo("93.184.216.34"), "other.example": _addrinfo("1.1.1.1")}
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: records[host])

    with net_safety._resolver_pinned_to_ips("pinned.example", ["93.184.216.34"]):
        assert socket.getaddrinfo("pinned.example", 443) == records["pinned.example"]
        assert socket.getaddrinfo("other.example", 443) == records["other.example"]
    assert socket.getaddrinfo("other.example", 443) == records["other.example"], "the resolver was not restored"


# ---------------------------------------------------------------------------
# The gzip cap is exact, and a bomb is never inflated past it (L232)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size, fits", [(1000, True), (1001, False)])
def test_the_gzip_cap_is_exact(size, fits):
    out = net_safety._inflate_capped(gzip.compress(b"x" * size), net_safety._GZIP_WBITS, 1000,
                                     "https://example.com/x")
    assert out == (b"x" * size if fits else None)


_real_decompressobj = zlib.decompressobj


class _RecordingInflater:
    """A real zlib decompressor that records every output limit asked of it."""

    def __init__(self, wbits, calls):
        self._d = _real_decompressobj(wbits)
        self._calls = calls

    @property
    def unconsumed_tail(self):
        return self._d.unconsumed_tail

    def decompress(self, data, max_length=0):
        self._calls.append(max_length)
        return self._d.decompress(data, max_length)

    def flush(self):
        self._calls.append("flush")
        return self._d.flush()


def test_a_compression_bomb_is_never_inflated_past_the_cap(monkeypatch):
    """To zlib, max_length 0 means unlimited, and flush() inflates whatever input
    is left. Either would unpack the whole bomb in memory before the size check
    returned None, so the result alone cannot tell."""
    calls = []
    monkeypatch.setattr(zlib, "decompressobj", lambda wbits: _RecordingInflater(wbits, calls))
    bomb = gzip.compress(b"\0" * 10_000_000)

    assert net_safety._inflate_capped(bomb, net_safety._GZIP_WBITS, 1000, "https://example.com/x") is None
    assert calls and all(isinstance(c, int) and 0 < c <= 1001 for c in calls), calls


# ---------------------------------------------------------------------------
# Link normalisation and canonical form (L68, L70, L91-L96)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, base, expected", [
    ("//cdn.example.com/img.jpg", "", "https://cdn.example.com/img.jpg"),               # protocol-relative
    ("https://example.com/a", "", "https://example.com/a"),                             # already absolute
    ("/images/hero.jpg", "https://example.com/feed/", "https://example.com/images/hero.jpg"),  # relative
    ("images/hero.jpg", "", None),                                                      # relative, no base
    ("", "https://example.com/", None),
])
def test_normalise_url_handles_each_link_shape(raw, base, expected):
    assert net_safety.normalise_url(raw, base_url=base) == expected


@pytest.mark.parametrize("raw, expected", [("  not a url  ", "not a url"), ("http://[::1", "http://[::1")])
def test_canonical_url_returns_an_unparseable_or_hostless_string_as_is(raw, expected):
    assert net_safety.canonical_url(raw) == expected
