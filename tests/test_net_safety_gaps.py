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
import contextlib
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


# A URL that still holds whitespace or a control character after trimming is
# two things stuck together, not a link. External scan, 2026-10-13: a feed
# entry link shaped that way travelled intact to a published Mastodon status.

_BREAK = chr(10) * 2


@pytest.mark.parametrize("raw", [
    "https://attacker.example/a" + _BREAK + "@victim@example.social #Injected",
    "https://attacker.example/a tail",
    "https://attacker.example/a" + chr(9) + "tail",
    "https://attacker.example/a" + chr(13) + "tail",
    "https://attacker.example/a" + chr(0) + "tail",
])
def test_normalise_url_rejects_embedded_whitespace_and_controls(raw):
    assert net_safety.normalise_url(raw, base_url="https://feed.example/rss") is None


@pytest.mark.parametrize("raw, expected", [
    ("https://example.com/article", True),
    ("http://example.com/a?b=c#d", True),
    ("https://example.com/a" + _BREAK + "@victim #x", False),
    ("https://example.com/a b", False),
    ("https://[", False),                 # urlparse raises; callers must not
    ("javascript:alert(1)", False),       # parseable, wrong scheme
    ("https://", False),                  # scheme but no host
    ("example.com/a", False),             # no scheme
    ("", False),
])
def test_is_single_clean_url(raw, expected):
    assert net_safety.is_single_clean_url(raw) is expected


# The first version of the check was the ASCII class \x00-\x20\x7F.
# Codex caught what it misses: these all render as a break or a space while
# sitting outside that range, so the injection #191 set out to close stayed
# open through any of them. No ASCII whitespace in these payloads -- that is
# the point, an ASCII space would have been caught for the wrong reason.

_SEPARATORS = [
    # chr() rather than the literal characters: these are invisible in source,
    # which is exactly how the gap survived review the first time.
    ("U+2028 line separator", chr(0x2028)),
    ("U+2029 paragraph separator", chr(0x2029)),
    ("U+0085 next line", chr(0x0085)),
    ("U+00A0 no-break space", chr(0x00A0)),
    ("U+1680 ogham space mark", chr(0x1680)),
    ("U+3000 ideographic space", chr(0x3000)),
    ("U+200B zero-width space", chr(0x200B)),
    ("U+202E right-to-left override", chr(0x202E)),
    ("U+FEFF byte order mark", chr(0xFEFF)),
]


@pytest.mark.parametrize("label, sep", _SEPARATORS)
def test_a_unicode_separator_does_not_survive_normalisation(label, sep):
    raw = "https://attacker.example/a" + sep + "@victim@example.social" + sep + "#Injected"
    assert net_safety.normalise_url(raw, base_url="https://feed.example/rss") is None, label


@pytest.mark.parametrize("label, sep", _SEPARATORS)
def test_a_unicode_separator_is_not_a_single_clean_url(label, sep):
    raw = "https://attacker.example/a" + sep + "@victim@example.social"
    assert net_safety.is_single_clean_url(raw) is False, label


@pytest.mark.parametrize("url", [
    "https://example.com/article",
    "https://arxiv.org/abs/2609.06391",
    "https://example.com/a?b=c#d",
    "https://münchen.example/café",     # internationalised, must still pass
    "https://example.com/中文",           # non-ASCII path, must still pass
])
def test_the_separator_check_does_not_over_reject(url):
    """Letters, marks, digits, punctuation and symbols all pass, ASCII or not."""
    assert net_safety.is_single_clean_url(url) is True
    assert net_safety.normalise_url(url, base_url="https://feed.example/rss") == url


@pytest.mark.parametrize("raw, expected", [("  not a url  ", "not a url"), ("http://[::1", "http://[::1")])
def test_canonical_url_returns_an_unparseable_or_hostless_string_as_is(raw, expected):
    assert net_safety.canonical_url(raw) == expected


# ===========================================================================
# External scan sweep, 2026-10-13/14.
# ===========================================================================

# -- The public-address predicate -------------------------------------------
# It denied a list of flags instead of also requiring is_global, so CGNAT
# shared space and deprecated IPv6 site-local passed as public.

@pytest.mark.parametrize("ip, public", [
    ("93.184.216.34", True),            # ordinary public IPv4
    ("8.8.8.8", True),
    ("2606:4700:4700::1111", True),     # ordinary public IPv6
    ("100.64.0.1", False),              # CGNAT / RFC 6598 shared space
    ("100.127.255.254", False),         # ... and its far end
    ("fec0::1", False),                 # deprecated IPv6 site-local
    ("64:ff9b::1.2.3.4", False),        # NAT64: is_global says True, is_reserved saves us
    ("10.0.0.1", False),
    ("172.16.0.1", False),
    ("169.254.169.254", False),         # cloud metadata
    ("127.0.0.1", False),
    ("::1", False),
    ("2001:db8::1", False),
    ("::ffff:127.0.0.1", False),        # IPv4-mapped loopback
    ("224.0.0.1", False),
    ("0.0.0.0", False),
    ("not-an-ip", False),
])
def test_is_public_ip(ip, public):
    assert net_safety._is_public_ip(ip) is public


# -- The DNS-rebinding pin ---------------------------------------------------
# It compared str(host).lower() to the parsed hostname. httpcore passes a BYTES
# host, so str() rendered it as "b'example.com'", the guard took its passthrough
# branch on every request, and the pin never fired at all -- for ASCII hosts as
# well as internationalised ones. The scan reported only the IDNA half.

_REBOUND = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]


@pytest.fixture
def rebinding_resolver(monkeypatch):
    """A resolver that always answers with a link-local address."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: list(_REBOUND))


@pytest.mark.parametrize("host", [
    "example.com",
    b"example.com",                 # what httpcore actually passes
    b"example.com.",                # fully-qualified form
    "EXAMPLE.COM",
])
def test_the_pin_blocks_a_rebind_however_the_host_is_spelled(host, rebinding_resolver):
    with net_safety._resolver_pinned_to_ips("example.com", ["93.184.216.34"]):
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(host, 443)


@pytest.mark.parametrize("host", ["münchen.example", b"xn--mnchen-3ya.example"])
def test_the_pin_blocks_a_rebind_on_an_internationalised_host(host, rebinding_resolver):
    with net_safety._resolver_pinned_to_ips("münchen.example", ["93.184.216.34"]):
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo(host, 443)


# -- A partially-shifted answer, the CDN case (#212) -------------------------
# Until 2026-10-21 the pin refused any answer containing an address the first
# resolution had not seen, which blocked www.theregister.com on 3 of 12 runs and
# www.microsoft.com on another. Nothing covered the mixed answer: every
# rebinding fixture returns ONLY the attacker address, so both the old strict
# check and the filtered invariant pass those. These pin the difference.

_GOOD = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
_ALSO_GOOD = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", 443))
_EVIL = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))


@pytest.fixture
def mixed_resolver(monkeypatch):
    """A rotating CDN: one pinned address, one the first lookup never saw."""
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [_GOOD, _ALSO_GOOD])


def test_a_rotated_answer_resolves_instead_of_failing_the_whole_request(mixed_resolver):
    with net_safety._resolver_pinned_to_ips("example.com", ["93.184.216.34"]):
        got = socket.getaddrinfo(b"example.com", 443)
    assert got == [_GOOD]


def test_a_rotated_answer_can_never_reach_the_unpinned_address(mixed_resolver):
    """The invariant: only prevalidated addresses are handed back."""
    with net_safety._resolver_pinned_to_ips("example.com", ["93.184.216.34"]):
        addresses = {entry[4][0] for entry in socket.getaddrinfo(b"example.com", 443)}
    assert addresses == {"93.184.216.34"}
    assert "93.184.216.35" not in addresses


def test_an_answer_mixing_a_pinned_address_with_a_hostile_one_drops_the_hostile_one(monkeypatch):
    """The shape an attacker would try once strict containment is gone."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_GOOD, _EVIL])
    with net_safety._resolver_pinned_to_ips("example.com", ["93.184.216.34"]):
        got = socket.getaddrinfo(b"example.com", 443)
    assert got == [_GOOD]
    assert all(entry[4][0] != "169.254.169.254" for entry in got)


def _captured_warnings(monkeypatch):
    """Collect SafeLogger.warn events. The event name is a log-record attribute,
    not message text, so caplog.text never contains it."""
    seen = []
    monkeypatch.setattr(net_safety.SafeLogger, "warn",
                        lambda event, *a, **k: seen.append((event, k)))
    return seen


def test_narrowing_an_answer_is_logged_so_a_rotating_cdn_is_not_invisible(mixed_resolver, monkeypatch):
    seen = _captured_warnings(monkeypatch)
    with net_safety._resolver_pinned_to_ips("example.com", ["93.184.216.34"]):
        socket.getaddrinfo(b"example.com", 443)
    events = [event for event, _ in seen]
    assert "resolver_answer_narrowed" in events
    fields = next(k for event, k in seen if event == "resolver_answer_narrowed")
    assert fields["dropped"] == 1 and fields["kept"] == 1


def test_an_unshifted_answer_logs_nothing(monkeypatch):
    """No warning when the resolver simply agrees with itself."""
    seen = _captured_warnings(monkeypatch)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_GOOD])
    with net_safety._resolver_pinned_to_ips("example.com", ["93.184.216.34"]):
        socket.getaddrinfo(b"example.com", 443)
    assert [event for event, _ in seen] == []


def test_the_pin_leaves_other_hostnames_alone(rebinding_resolver):
    """The pin is a global monkeypatch, so an unrelated lookup must pass through
    -- other tasks resolve while a pinned fetch is in flight."""
    with net_safety._resolver_pinned_to_ips("example.com", ["93.184.216.34"]):
        assert socket.getaddrinfo("other.example", 443) == list(_REBOUND)


def test_the_pin_matches_the_host_shape_httpcore_really_passes(monkeypatch):
    """Pins the integration, not a fixture.

    The bug was a mismatch between what the pin expected and what the HTTP
    stack passes. A hand-written b"example.com" fixture would drift from a
    future httpx; this asserts against the live call. No network: the spy
    raises before any connection is made.
    """
    seen = []
    original = socket.getaddrinfo

    def spy(host, *args, **kwargs):
        seen.append(host)
        raise socket.gaierror("intercepted")

    monkeypatch.setattr(socket, "getaddrinfo", spy)

    async def attempt():
        async with httpx.AsyncClient(timeout=3) as client:
            with contextlib.suppress(Exception):
                await client.get("https://example.com/x")

    asyncio.run(attempt())
    monkeypatch.setattr(socket, "getaddrinfo", original)

    assert seen, "httpx did not reach socket.getaddrinfo; the pin's patch point moved"
    keys = net_safety._resolver_host_keys("example.com")
    assert net_safety._resolver_host_key(seen[0]) in keys


# -- The total fetch deadline ------------------------------------------------
# Per-hop timeouts are inactivity timeouts: a slow-drip server stays under them
# forever while holding the process-wide resolver pin.

@pytest.fixture
def stubbed_fetch_path(monkeypatch):
    """Let get_with_safe_redirects run for real with the network stubbed out."""
    monkeypatch.setattr(net_safety, "is_safe_public_url", lambda _u: True)
    monkeypatch.setattr(net_safety, "is_allowed_metadata_fetch_url", lambda _u: True)
    monkeypatch.setattr(net_safety, "_resolve_public_ip_candidates",
                        lambda _h: ["93.184.216.34"])


def test_a_fetch_that_outlives_its_deadline_returns_none(monkeypatch, stubbed_fetch_path):
    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(30)
        raise AssertionError("should not get here")

    monkeypatch.setattr(net_safety, "_capped_stream_get", never_finishes)

    async def go():
        async with httpx.AsyncClient() as client:
            return await net_safety.get_with_safe_redirects(
                client, "https://example.com/slow", total_timeout=0.05)

    assert asyncio.run(go()) is None


def test_the_deadline_does_not_interfere_with_a_prompt_fetch(monkeypatch, stubbed_fetch_path):
    sentinel = httpx.Response(200, content=b"ok")

    async def prompt(*_args, **_kwargs):
        return sentinel

    monkeypatch.setattr(net_safety, "_capped_stream_get", prompt)

    async def go():
        async with httpx.AsyncClient() as client:
            return await net_safety.get_with_safe_redirects(
                client, "https://example.com/fast", total_timeout=5)

    assert asyncio.run(go()) is sentinel


def test_a_queued_fetch_keeps_its_whole_budget(monkeypatch, stubbed_fetch_path):
    """The budget starts after resolver-lock admission, not before it.

    Codex, on #192: the deadline wrapped the wait for the global resolver pin,
    so with feeds fetched concurrently the tasks queued behind one slow-drip
    origin burned their identical budgets waiting and expired without ever
    attempting their own feed. One bad host discarded the whole batch.

    Here the slow fetch holds the pin for 0.3s while the queued one is given a
    0.2s budget. Under the old wrapper it expired; it must now succeed, because
    its 0.2s does not start until it holds the lock.
    """
    fast = httpx.Response(200, content=b"fast")

    async def by_url(_client, url, **_kwargs):
        if "slow" in url:
            await asyncio.sleep(0.3)
            return httpx.Response(200, content=b"slow")
        await asyncio.sleep(0.01)
        return fast

    monkeypatch.setattr(net_safety, "_capped_stream_get", by_url)

    async def go():
        async with httpx.AsyncClient() as client:
            slow = net_safety.get_with_safe_redirects(
                client, "https://slow.example/feed", total_timeout=5)
            queued = net_safety.get_with_safe_redirects(
                client, "https://fast.example/feed", total_timeout=0.2)
            return await asyncio.gather(slow, queued)

    slow_result, queued_result = asyncio.run(go())

    assert slow_result is not None
    assert queued_result is fast, "the queued fetch was charged for waiting on the lock"


def test_an_exhausted_budget_stops_a_redirect_chain(monkeypatch, stubbed_fetch_path):
    """The budget carries across hops, so a chain of slow redirects still ends."""
    hops = []

    async def slow_redirect(_client, url, **_kwargs):
        hops.append(url)
        await asyncio.sleep(0.08)
        return httpx.Response(
            302, headers={"location": f"https://example.com/{len(hops)}"},
            request=httpx.Request("GET", url))

    monkeypatch.setattr(net_safety, "_capped_stream_get", slow_redirect)

    async def go():
        async with httpx.AsyncClient() as client:
            return await net_safety.get_with_safe_redirects(
                client, "https://example.com/0", total_timeout=0.2)

    assert asyncio.run(go()) is None
    # Not the full max_redirects + 1: the budget ran out first.
    assert 1 <= len(hops) <= 4, hops
