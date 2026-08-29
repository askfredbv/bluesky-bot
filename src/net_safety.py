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
import zlib
from contextlib import contextmanager
from typing import Dict, List, Optional
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

    return not (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
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
        ip_str = result[4][0]
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
@contextmanager
def _resolver_pinned_to_ips(hostname: str, allowed_ips: List[str]):
    """
    Temporarily constrains DNS resolution for one hostname to a prevalidated set.
    If the resolver returns addresses outside the allowed set, resolution is blocked.
    """
    original_getaddrinfo = socket.getaddrinfo
    canonical_hostname = hostname.lower()
    allowed_set = set(allowed_ips)

    def guarded_getaddrinfo(host: str, *args, **kwargs):
        if str(host).lower() != canonical_hostname:
            return original_getaddrinfo(host, *args, **kwargs)

        current = original_getaddrinfo(host, *args, **kwargs)
        current_ips = {entry[4][0] for entry in current}
        if not current_ips:
            raise socket.gaierror(f"No DNS records found for {host}")

        unexpected = current_ips - allowed_set
        if unexpected:
            raise socket.gaierror(f"Resolver returned unexpected address for {host}")

        filtered = [entry for entry in current if entry[4][0] in allowed_set]
        if not filtered:
            raise socket.gaierror(f"No allowed DNS records found for {host}")
        return filtered

    socket.getaddrinfo = guarded_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


# Hard cap on any single fetched body. Feeds are typically <1 MB, article HTML
# <2 MB, and OG images are separately capped below 1 MB, so 5 MB is generous for
# legitimate content while stopping a compromised source from OOM-ing the runner
# with a multi-GB body (the size check used to happen only AFTER the full read).
MAX_FETCH_BYTES = 5_000_000


def _inflate_capped(raw: bytes, wbits: int, cap: int) -> Optional[bytes]:
    """Decompress ``raw`` but stop and return None if the output exceeds ``cap``
    — so a compression bomb (small compressed, huge decompressed) cannot allocate
    unbounded memory. Returns None on any decompression error too."""
    try:
        d = zlib.decompressobj(wbits)
        out = bytearray(d.decompress(raw, cap + 1))
        while d.unconsumed_tail and len(out) <= cap:
            out += d.decompress(d.unconsumed_tail, cap + 1 - len(out))
        out += d.flush()
    except zlib.error:
        return None
    return None if len(out) > cap else bytes(out)


# gzip (and x-gzip) use a zlib stream with a gzip header: wbits = 31.
_GZIP_WBITS = 31


async def _capped_stream_get(client: httpx.AsyncClient, url: str, *,
                             headers: Optional[Dict[str, str]], timeout: float,
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
            content = _inflate_capped(bytes(raw), _GZIP_WBITS, max_bytes)
            if content is None:
                SafeLogger.warn("response_too_large",
                                "Response exceeds size cap (decompressed) or is a compression bomb",
                                url=url, cap_bytes=max_bytes)
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


async def get_with_safe_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    max_redirects: int = 5,
    enforce_metadata_policy: bool = True,
    max_bytes: int = MAX_FETCH_BYTES
) -> Optional[httpx.Response]:
    """
    Fetch a URL while validating each redirect target and disallowing scheme changes.

    ``enforce_metadata_policy`` toggles the METADATA_FETCH allow/block domain
    list (the extra restriction for scraping arbitrary article URLs). RSS feed
    fetches pass ``False`` — they are a fixed, trusted source list, so the
    domain allowlist does not apply — but they still get the full public-IP
    validation, DNS pinning, and per-hop redirect checks (the SSRF guard).
    """
    current_url = url
    initial_scheme = urlparse(url).scheme

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
                    response = await _capped_stream_get(
                        client, current_url,
                        headers=headers, timeout=timeout, max_bytes=max_bytes,
                    )
        except Exception as e:
            SafeLogger.warn("dns_validated_request_blocked", "Blocked URL request after DNS validation", url=current_url, error_type=type(e).__name__)
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
