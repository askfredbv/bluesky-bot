"""Network-safety primitives: SSRF-guarded fetching for the bot.

URL canonicalisation/normalisation, a public-IP allowlist that rejects
private/loopback/link-local addresses across every resolved record, DNS pinning
to defeat rebinding (serialised per running loop because it swaps the global
resolver), and a safe-redirect fetcher that revalidates every hop.

Extracted from src/utils.py to give the security-critical code one findable
home. Imports nothing from src.utils.
"""
import asyncio
import ipaddress
import re
import socket
import time
import unicodedata
import zlib
from contextlib import contextmanager
from typing import Dict, List, Optional, Union
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from src.config import METADATA_FETCH_ALLOWED_DOMAINS, METADATA_FETCH_BLOCKED_DOMAINS
from src.logger import SafeLogger

_ARXIV_CANONICAL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)


# _resolver_pinned_to_ips swaps the PROCESS-GLOBAL socket.getaddrinfo for the
# duration of a pinned request. That is not safe under concurrency: fetch_news
# runs feeds in parallel, and overlapping pins would clobber each other's global
# resolver — silently disabling the SSRF guard for the overlapping fetch. The
# lock below serialises the pinned section so only one pinned request is in
# flight at a time (feeds run twice a day, so the latency cost is irrelevant).
# It is created lazily per running loop: a module-level asyncio.Lock() would
# bind to the first loop and break across multiple asyncio.run() calls.
_RESOLVER_PIN_LOCK: Optional[asyncio.Lock] = None
_RESOLVER_PIN_LOCK_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _resolver_pin_lock() -> asyncio.Lock:
    global _RESOLVER_PIN_LOCK, _RESOLVER_PIN_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _RESOLVER_PIN_LOCK is None or _RESOLVER_PIN_LOCK_LOOP is not loop:
        _RESOLVER_PIN_LOCK = asyncio.Lock()
        _RESOLVER_PIN_LOCK_LOOP = loop
    return _RESOLVER_PIN_LOCK
def canonical_url(url: str) -> str:
    """Canonicalise a URL for deduplication.

    arXiv: abs/pdf/html forms with or without version suffix collapse to
    'arxiv:<id>'. Everything else: https scheme, lowercased host, query and
    fragment stripped, trailing slash removed. Path case preserved per RFC 3986.
    Returns empty string for empty input; returns the stripped input unchanged
    when parsing fails or no host is present.
    """
    if not url:
        return ""
    trimmed = url.strip()
    match = _ARXIV_CANONICAL_RE.search(trimmed)
    if match:
        return f"arxiv:{match.group(1)}"
    try:
        parsed = urlparse(trimmed)
    except Exception:
        return trimmed
    if not parsed.netloc:
        return trimmed
    return urlunparse((
        "https",
        parsed.netloc.lower(),
        parsed.path.rstrip("/"),
        "",
        "",
        "",
    ))
# Characters a URL must not carry once trimmed: every Unicode space separator
# (Zs, Zl, Zp) and every control or format character (Cc, Cf). RFC 3986 wants
# these percent-encoded, so a value still holding one is not a link that needs
# fixing -- it is two things stuck together. Feed links cross an untrusted
# boundary and get concatenated into post text downstream, where a break turns
# whatever follows into its own line of published content.
#
# This started as the ASCII class [\x00-\x20\x7F] (#191). Codex caught what
# that misses: U+2028, U+2029, U+0085 and U+00A0 all render as a break or a
# space while sitting outside it, so the injection #191 set out to close stayed
# open through any of them. Categories rather than a longer character list,
# because the list is what drifted. Cf also covers the bidi overrides (U+202E
# and friends), which can make a rendered URL read as something it is not.
#
# Nothing legitimate is caught: letters, marks, digits, punctuation and symbols
# all pass, including non-ASCII ones in an internationalised URL.
_URL_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Zs"})


def _has_unsafe_url_char(value: str) -> bool:
    """True if ``value`` holds any separator, control or format character."""
    return any(unicodedata.category(ch) in _URL_UNSAFE_CATEGORIES for ch in value)


def is_single_clean_url(url: str) -> bool:
    """True if ``url`` is exactly one parseable http(s) URL.

    "One" is the load-bearing word. Callers that concatenate a URL into text a
    reader will see need to know they are appending a link and nothing else.

    External scan, 2026-10-13: an RSS entry link of the form
    "https://host/article<newline><newline>@victim@example.social #Injected"
    reached a published Mastodon status intact, because every step between the
    feed and the status only ever trimmed the ends of the string.
    """
    if not url or _has_unsafe_url_char(url):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        # urlparse raises on malformed IPv6 ("https://["); callers must not.
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def normalise_url(url: str, base_url: str = "") -> Optional[str]:
    """
    Normalises a raw URL extracted from HTML into a fully qualified URL.
    Handles three cases that would otherwise crash httpx:
      - Protocol-relative: //cdn.example.com/img.jpg  -> https://cdn.example.com/img.jpg
      - Relative path:     /images/hero.jpg           -> https://example.com/images/hero.jpg
      - Already absolute:  https://example.com/img    -> unchanged
    Returns None if the URL is empty or unfixable.
    """
    if not url:
        return None
    url = url.strip()
    # Reject before any branch below: every return value derives from this
    # string, so one check here covers the absolute, protocol-relative and
    # urljoin paths alike.
    if _has_unsafe_url_char(url):
        SafeLogger.warn("url_rejected_unsafe_chars",
                        "Rejected URL containing whitespace or control characters",
                        url=url[:200])
        return None
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith(('http://', 'https://')):
        return url
    if base_url:
        return urljoin(base_url, url)
    # Can't resolve a relative URL without a base — skip it
    SafeLogger.warn("relative_url_without_base", "Could not normalise relative URL without base", url=url)
    return None
def is_safe_public_url(url: str) -> bool:
    """
    Validates that a URL is public and safe to request.
    Rejects non-HTTP(S), localhost-like hosts, and private/non-routable IPs.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    if hostname.lower() in {"localhost"}:
        return False

    return _resolve_public_ip_candidates(hostname) is not None
def _is_public_ip(ip_str: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    # is_global AND the explicit denies, not either alone.
    #
    # The denies alone let CGNAT shared space (100.64.0.0/10) and deprecated
    # IPv6 site-local (fec0::/10) through: Python classifies neither as private,
    # and both are routable inside a carrier or corporate network. An external
    # scan flagged the CGNAT half on 2026-10-13.
    #
    # is_global alone is worse: Python reports the NAT64 well-known prefix
    # (64:ff9b::/96) as global, so switching to it outright would newly ALLOW
    # an address that maps straight onto internal IPv4. is_reserved catches
    # that one, which is why both halves stay.
    return ip_obj.is_global and not (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
        # IPv6-only attribute; absent on IPv4Address.
        or getattr(ip_obj, "is_site_local", False)
    )
def _resolve_public_ip_candidates(hostname: str) -> Optional[List[str]]:
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None
    except Exception:
        return None

    candidate_ips: List[str] = []
    seen: set[str] = set()
    for result in resolved:
        # sockaddr[0] is the host; typeshed widens it to str | int (AF_NETLINK
        # etc.), but for the AF_INET/AF_INET6 records we resolve it is always
        # the IP string. str() is a no-op on those and keeps the set/list typed.
        ip_str = str(result[4][0])
        if ip_str in seen:
            continue
        seen.add(ip_str)
        if not _is_public_ip(ip_str):
            return None
        candidate_ips.append(ip_str)

    return candidate_ips or None
def _hostname_matches_policy(hostname: str, domain_rule: str) -> bool:
    normalized_hostname = hostname.lower().strip(".")
    normalized_rule = domain_rule.lower().strip().strip(".")
    if not normalized_rule:
        return False
    return normalized_hostname == normalized_rule or normalized_hostname.endswith(f".{normalized_rule}")
def is_allowed_metadata_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return False

    if any(_hostname_matches_policy(hostname, domain) for domain in METADATA_FETCH_BLOCKED_DOMAINS):
        SafeLogger.warn("domain_policy_blocked", "Blocked URL by metadata domain policy denylist", url=url, hostname=hostname)
        return False

    if METADATA_FETCH_ALLOWED_DOMAINS and not any(
        _hostname_matches_policy(hostname, domain) for domain in METADATA_FETCH_ALLOWED_DOMAINS
    ):
        SafeLogger.warn("domain_policy_blocked", "Blocked URL by metadata domain policy allowlist", url=url, hostname=hostname)
        return False

    return True
def _resolver_host_key(host: object) -> str:
    """Normalise a getaddrinfo host argument so it can be compared to the pin."""
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "ignore")
    return str(host).lower().rstrip(".")


def _resolver_host_keys(hostname: str) -> frozenset:
    """Every spelling of ``hostname`` a resolver call might arrive as.

    httpcore hands socket.getaddrinfo a BYTES host, already IDNA-encoded, not
    the str that urlparse produced. The pin used to compare
    ``str(host).lower()`` against the parsed hostname, so a bytes host rendered
    as "b'example.com'" and never matched: guarded_getaddrinfo took its
    passthrough branch on every request and the DNS-rebinding guard never fired
    at all -- for ASCII hostnames too, not only internationalised ones.
    Verified against the locked httpx 0.28.1 / httpcore 1.0.9.

    An external scan (2026-10-13) reported only the IDNA half of this. The
    bytes half is what makes it total, which is why both forms are matched
    here and why test_net_safety_gaps pins the exact call shape httpcore uses.
    """
    base = hostname.lower().rstrip(".")
    keys = {base}
    try:
        keys.add(base.encode("idna").decode("ascii").lower())
    except (UnicodeError, ValueError):
        # Unencodable label: the raw form is still a valid key to match on.
        pass
    return frozenset(keys)


@contextmanager
def _resolver_pinned_to_ips(hostname: str, allowed_ips: List[str]):
    """
    Temporarily constrains DNS resolution for one hostname to a prevalidated set.

    The invariant is that a connection only ever goes to an address this request
    already validated as public: the guard returns ONLY entries from
    ``allowed_ips``, and raises when that intersection is empty.

    It does NOT refuse an answer merely because it also contains addresses the
    first resolution did not. Until 2026-10-21 it did, on strict set
    containment, and that cost real feeds: `www.theregister.com` (12 advertised
    addresses) was blocked on 3 of 12 runs and `www.microsoft.com` (5) on
    another, because a resolver answering with a shifting subset between the two
    lookups trips containment while the intersection is still perfectly good.
    Each block silently removed a configured source from that run's candidate
    pool (#212).

    Dropping that check costs no security. A rebinding attack wins only if the
    connection REACHES an attacker address, and ``filtered`` makes that
    impossible whatever else the answer contains; a full rebind, which is what
    an attacker actually returns, still leaves an empty intersection and still
    raises. Blocking a mixed answer refused a request we were always going to
    send to a prevalidated address.
    """
    original_getaddrinfo = socket.getaddrinfo
    pinned_keys = _resolver_host_keys(hostname)
    allowed_set = set(allowed_ips)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if _resolver_host_key(host) not in pinned_keys:
            return original_getaddrinfo(host, *args, **kwargs)

        current = original_getaddrinfo(host, *args, **kwargs)
        current_ips = {entry[4][0] for entry in current}
        if not current_ips:
            raise socket.gaierror(f"No DNS records found for {host}")

        # The guard: hand back prevalidated addresses only. An empty
        # intersection is a real rebind (or a hostname that moved mid-request)
        # and still fails closed.
        filtered = [entry for entry in current if entry[4][0] in allowed_set]
        if not filtered:
            raise socket.gaierror(f"No allowed DNS records found for {host}")

        # A partially-shifted answer is now served rather than refused, so say
        # so: without this line a rotating CDN and a correctly-working pin are
        # indistinguishable in the log, and the 2026-10-21 blocks were only
        # diagnosable because the refusal was loud.
        dropped = current_ips - allowed_set
        if dropped:
            SafeLogger.warn(
                "resolver_answer_narrowed",
                "Resolver returned addresses outside the pinned set; connecting to the pinned ones",
                # _resolver_host_key, not str(host): httpcore passes BYTES, and
                # str() on those renders "b'example.com'" -- the exact shape
                # whose use in a COMPARISON meant the pin never fired at all
                # before #192. Harmless in a log field, misleading in a log
                # someone reads to diagnose this.
                host=_resolver_host_key(host), dropped=len(dropped), kept=len(filtered))
        return filtered

    # Monkeypatching the module attribute: guarded_getaddrinfo intentionally
    # has a looser (*args/**kwargs) signature than socket.getaddrinfo.
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


# Hard cap on any single fetched body. Feeds are typically <1 MB, article HTML
# <2 MB, and OG images are separately capped below 1 MB, so 5 MB is generous for
# legitimate content while stopping a compromised source from OOM-ing the runner
# with a multi-GB body (the size check used to happen only AFTER the full read).
MAX_FETCH_BYTES = 5_000_000


def _inflate_capped(raw: bytes, wbits: int, cap: int, url: str) -> Optional[bytes]:
    """Decompress ``raw`` but stop and return None if the output exceeds ``cap``
    — so a compression bomb (small compressed, huge decompressed) cannot allocate
    unbounded memory. Returns None on any decompression error too."""
    try:
        d = zlib.decompressobj(wbits)
        out = bytearray(d.decompress(raw, cap + 1))
        while d.unconsumed_tail and len(out) <= cap:
            out += d.decompress(d.unconsumed_tail, cap + 1 - len(out))
        # Bail BEFORE flush(): a no-arg flush() would decompress all remaining
        # unconsumed input unbounded, re-introducing the bomb. If we are already
        # over the cap (compressed tail still pending), refuse now.
        if len(out) > cap:
            _log_over_cap(url, cap)
            return None
        out += d.flush()  # unconsumed_tail is empty here -> bounded output
    except zlib.error as e:
        # A malformed gzip body is a different failure than an oversize one —
        # keep the zlib message so feed-health can tell them apart.
        SafeLogger.warn("gzip_decode_failed", "Malformed gzip response",
                        url=url, error_msg=str(e)[:200])
        return None
    if len(out) > cap:
        _log_over_cap(url, cap)
        return None
    return bytes(out)


def _log_over_cap(url: str, cap: int) -> None:
    SafeLogger.warn("response_too_large",
                    "gzip content exceeds size cap (decompressed / compression bomb)",
                    url=url, cap_bytes=cap)


# gzip (and x-gzip) use a zlib stream with a gzip header: wbits = 31.
_GZIP_WBITS = 31


async def _capped_stream_get(client: httpx.AsyncClient, url: str, *,
                             headers: Optional[Dict[str, str]],
                             timeout: Union[float, httpx.Timeout, None],
                             max_bytes: int) -> Optional[httpx.Response]:
    """GET with redirects disabled that aborts if the body exceeds ``max_bytes``.

    Caps the RAW (still-compressed) stream, not the decoded stream: httpx's
    aiter_bytes() decompresses each chunk before yielding, so a compression bomb
    could allocate gigabytes from one small chunk before a decoded-size check
    ran. We request identity encoding, cap aiter_raw() at max_bytes, and if a
    non-compliant server compressed anyway (gzip) we inflate with an output cap.

    A redirect is returned unread (only its headers matter); a final response is
    returned as a fully-read Response with content-encoding resolved. Returns
    None (logged) if the response declares/streams/decompresses past the cap or
    uses an encoding we do not bound.
    """
    req_headers = dict(headers or {})
    req_headers.setdefault("Accept-Encoding", "identity")
    async with client.stream("GET", url, headers=req_headers, timeout=timeout,
                             follow_redirects=False) as response:
        if response.is_redirect:
            return response
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    SafeLogger.warn("response_too_large",
                                    "Response exceeds size cap (declared Content-Length)",
                                    url=url, cap_bytes=max_bytes)
                    return None
            except ValueError:
                pass
        raw = bytearray()
        async for chunk in response.aiter_raw():
            raw += chunk
            if len(raw) > max_bytes:
                SafeLogger.warn("response_too_large",
                                "Response exceeds size cap (streamed raw)",
                                url=url, cap_bytes=max_bytes)
                return None

        encoding = response.headers.get("content-encoding", "").lower().strip()
        if encoding in ("", "identity"):
            content: Optional[bytes] = bytes(raw)
        elif encoding in ("gzip", "x-gzip"):
            # _inflate_capped logs the specific reason (decode error vs oversize).
            content = _inflate_capped(bytes(raw), _GZIP_WBITS, max_bytes, url)
            if content is None:
                return None
        else:
            # We asked for identity; a server forcing br/zstd/deflate anyway is
            # rare. Rather than decode it unbounded, refuse (fail safe).
            SafeLogger.warn("unsupported_content_encoding",
                            "Refusing response with unbounded content-encoding",
                            url=url, encoding=encoding)
            return None

        out_headers = httpx.Headers(response.headers)
        out_headers.pop("content-encoding", None)  # content is now decoded
        out_headers.pop("content-length", None)
        return httpx.Response(status_code=response.status_code,
                              headers=out_headers, content=content,
                              request=response.request)


# Working-time budget for one fetch, redirects and body read included.
#
# The per-hop timeouts are inactivity timeouts: a server that sends one byte
# inside every read window keeps the connection alive indefinitely, and because
# the resolver pin is held across the request (see _resolver_pin_lock) a single
# slow-drip host stalls every other fetch behind it. 45s is far above anything
# legitimate here (a feed answers in under 5) and bounds the damage. Flagged by
# an external scan, 2026-10-13.
MAX_FETCH_SECONDS: float = 45.0


async def get_with_safe_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: Union[float, httpx.Timeout, None] = 10.0,
    max_redirects: int = 5,
    enforce_metadata_policy: bool = True,
    max_bytes: int = MAX_FETCH_BYTES,
    total_timeout: float = MAX_FETCH_SECONDS,
) -> Optional[httpx.Response]:
    """
    Fetch a URL while validating each redirect target and disallowing scheme changes.

    ``total_timeout`` bounds the time this fetch spends WORKING -- connecting,
    reading, following redirects -- and deliberately not the time it spends
    queued behind another fetch on the resolver pin lock.

    #192 wrapped the whole call in one asyncio.timeout, which charged a task
    for the wait. Codex caught the consequence: with six feeds fetched
    concurrently and one slow-drip origin holding the pin, the other five burn
    their identical budgets queueing and expire without ever attempting their
    own feed, so one bad host discards the whole batch instead of only itself.
    The budget is therefore started after lock admission and carried across
    hops, so a redirect chain still cannot run away.

    ``enforce_metadata_policy`` toggles the METADATA_FETCH allow/block domain
    list (the extra restriction for scraping arbitrary article URLs). RSS feed
    fetches pass ``False`` — they are a fixed, trusted source list, so the
    domain allowlist does not apply — but they still get the full public-IP
    validation, DNS pinning, and per-hop redirect checks (the SSRF guard).
    """
    current_url = url
    initial_scheme = urlparse(url).scheme
    budget = total_timeout

    for _ in range(max_redirects + 1):
        if enforce_metadata_policy and not is_allowed_metadata_fetch_url(current_url):
            return None

        parsed_current = urlparse(current_url)
        hostname = parsed_current.hostname
        if not hostname:
            SafeLogger.warn("unsafe_url_blocked", "Blocked unsafe URL request", url=current_url)
            return None

        candidate_ips = _resolve_public_ip_candidates(hostname)
        if not candidate_ips:
            SafeLogger.warn("unsafe_url_blocked", "Blocked unsafe URL request", url=current_url)
            return None

        try:
            # Serialise the global-resolver pin (see _resolver_pin_lock): the
            # pin must stay valid for the whole request, so hold the lock across
            # the await rather than only around the getaddrinfo swap.
            async with _resolver_pin_lock():
                with _resolver_pinned_to_ips(hostname, candidate_ips):
                    if budget <= 0:
                        raise TimeoutError("fetch budget exhausted before this hop")
                    # Clock starts here, after admission, and the elapsed time
                    # is charged back whatever the outcome.
                    started = time.monotonic()
                    try:
                        async with asyncio.timeout(budget):
                            response = await _capped_stream_get(
                                client, current_url,
                                headers=headers, timeout=timeout, max_bytes=max_bytes,
                            )
                    finally:
                        budget -= time.monotonic() - started
        except TimeoutError as e:
            # Before the broad handler below: TimeoutError is an Exception, and
            # a deadline is a different story from a blocked request.
            SafeLogger.warn("fetch_deadline_exceeded",
                            "Fetch exceeded its total working deadline; abandoning",
                            url=current_url, deadline_seconds=total_timeout,
                            error_type=type(e).__name__, error_msg=str(e)[:200])
            return None
        except Exception as e:
            SafeLogger.warn("dns_validated_request_blocked",
                            "Blocked URL request after DNS validation",
                            url=current_url, error_type=type(e).__name__,
                            # AGENTS.md #3: the type alone has cost this repo
                            # multi-day diagnoses more than once.
                            error_msg=str(e)[:200])
            return None

        # None => blocked by the size cap (logged in _capped_stream_get).
        if response is None:
            return None

        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                return response
            next_url = urljoin(str(response.url), location)

            parsed_next = urlparse(next_url)
            if parsed_next.scheme != initial_scheme:
                SafeLogger.warn("cross_scheme_redirect_blocked", "Blocked cross-scheme redirect", from_url=current_url, to_url=next_url)
                return None

            if enforce_metadata_policy and not is_allowed_metadata_fetch_url(next_url):
                return None

            if not is_safe_public_url(next_url):
                SafeLogger.warn("unsafe_redirect_target_blocked", "Blocked unsafe redirect target", url=next_url)
                return None

            current_url = next_url
            continue

        return response

    SafeLogger.warn("too_many_redirects_blocked", "Blocked URL due to too many redirects", url=url)
    return None
