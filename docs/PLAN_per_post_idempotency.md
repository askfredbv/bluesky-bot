# Plan: Per-post idempotency for thread broadcasts

## Why this exists

`retry_with_backoff` currently wraps the *whole* `post_to_bluesky` call. If post 3 of a 5-post thread triggers a 429 or network error, the retry replays the entire function — **re-sending posts 1 and 2 as duplicates.**

The same flaw exists on Mastodon, and is arguably more active there: `post_to_mastodon` already emits a `mastodon_partial_delivery` log event on mid-thread failure and re-raises — but the outer `@retry_with_backoff` catches that re-raise and calls the function from scratch. The partial-delivery log fires, then we re-post the already-delivered posts anyway. This is probably happening silently every time a Mastodon post 3/4 fails.

The 429 handling shipped in v4.9.1 acknowledged this as a known limitation. It stays a limitation today.

**Trigger to execute:** either of
- a **Bluesky** duplicate actually appears in production, OR
- a log-grep for `mastodon_partial_delivery` shows the bug has already fired silently in the last 30 days, OR
- engagement Slice A (`PLAN_engagement_feedback.md`) is being scheduled — that plan needs the same `List[str]` return-type change, so bundling them saves a second pass over `broadcasters.py` and `main.py`.

Until one of those fires, this is premature optimisation — threads are mostly 1–2 posts now (v4.14 single-post default), and rate limits haven't materialised.

---

## Approach

The fix is architectural, not additive. Move retry decisions **inside** the broadcaster functions at the per-post granularity, and drop the outer `@retry_with_backoff` wrapper.

### The shape change

**Before:**
```python
@retry_with_backoff              # ← outer retry replays the entire thread
async def post_to_bluesky(client, posts, ...):
    for post in posts:
        send(post)               # ← a failure here rewinds ALL sent posts
```

**After:**
```python
async def post_to_bluesky(client, posts, ...) -> BroadcastResult:
    sent: List[str] = []
    for i, post in enumerate(posts):
        try:
            uri = await _send_post_with_retry(client, post, reply_to=sent[-1] if sent else None)
            sent.append(uri)
        except Exception as e:
            SafeLogger.warn("bluesky_partial_delivery",
                            platform="bluesky",
                            posted=len(sent), expected=len(posts), stopped_at=i,
                            error_type=type(e).__name__)
            return BroadcastResult(client=client, sent_uris=sent, error=e)
    return BroadcastResult(client=client, sent_uris=sent, error=None)
```

Posts already on the wire stay there. Failures stop the thread cleanly with an explicit `bluesky_partial_delivery` log event. **Event name prefixed with platform** for symmetry with the existing `mastodon_partial_delivery`.

### Return type

Currently `post_to_bluesky` returns `client` (reused downstream by `handle_interactions`). `post_to_mastodon` returns nothing. The new signature must preserve the client re-use and surface sent URIs (needed by both this plan and engagement Slice A).

```python
@dataclass(frozen=True)
class BroadcastResult:
    client: Any                    # Bluesky: AsyncClient; Mastodon: None
    sent_uris: List[str]           # at://… for Bluesky; status IDs for Mastodon
    error: Optional[Exception]     # None on clean delivery; populated on partial
```

`main.py:227` changes from:
```python
bsky_broadcast_client = results[0] if not isinstance(results[0], Exception) else content_prep.bsky_client
```
to:
```python
bsky_result = results[0] if not isinstance(results[0], Exception) else None
bsky_broadcast_client = bsky_result.client if bsky_result else content_prep.bsky_client
```

`BroadcastPayload` gains `bsky_sent_uris: List[str]` and `mastodon_sent_ids: List[str]` for downstream consumers (engagement metrics, partial-delivery telemetry).

### The retry helpers — split, not merged

The current `retry_with_backoff` body has two distinct strategies (429 vs transient) sharing control flow. Extract them as separate functions rather than one god-helper:

```python
def classify_retry(e: Exception) -> Literal["rate_limit", "transient"]:
    """Duck-type inspect e.response.status_code to decide retry strategy."""

async def sleep_for_rate_limit(attempt: int, exception: Exception) -> None:
    """Honour Retry-After / ratelimit-reset headers; fall back to
    RATE_LIMIT_BASE_WAIT_SECONDS * attempt. Raises if attempt > RATE_LIMIT_MAX_RETRIES."""

async def sleep_for_transient(attempt: int) -> None:
    """BACKOFF_FACTOR ** attempt + jitter. Raises if attempt >= MAX_API_RETRIES."""
```

Per-post retry loop uses them directly:
```python
async def _send_post_with_retry(client, post, reply_to):
    transient_attempt = 0
    rate_limit_attempt = 0
    while True:
        try:
            return (await client.send_post(...)).uri
        except Exception as e:
            kind = classify_retry(e)
            if kind == "rate_limit":
                rate_limit_attempt += 1
                await sleep_for_rate_limit(rate_limit_attempt, e)  # raises when exhausted
            else:
                transient_attempt += 1
                await sleep_for_transient(transient_attempt)       # raises when exhausted
```

`retry_with_backoff` stays as a thin decorator calling the same helpers — existing call sites (`get_link_metadata`, `update_profile_bio`, etc.) keep their semantics unchanged.

**Also fix the existing off-by-one inconsistency while we're in there**: `rate_limit_retries > RATE_LIMIT_MAX_RETRIES` (strict) vs `retries >= MAX_API_RETRIES` (non-strict). Pick one — strict-greater reads more naturally as "attempt N+1 is too many."

### Mastodon inner retry — extend, don't bypass

`_status_post_with_timeout_and_retry` currently has its own 3-attempt loop with `min(2^n, 5)` seconds between tries — **no 429 awareness, no Retry-After handling.** Simply dropping the outer `@retry_with_backoff` on `post_to_mastodon` means losing all 429 handling on Mastodon.

Fix: replace the inner loop's sleep with `sleep_for_rate_limit` / `sleep_for_transient` via `classify_retry`. Same helpers, same semantics as the Bluesky path. Mastodon gets a *stronger* retry story than it has today, not a weaker one.

---

## Files to change

| File | Change |
|---|---|
| `src/utils.py` | Extract `classify_retry`, `sleep_for_rate_limit`, `sleep_for_transient`; refactor `retry_with_backoff` to call them; fix the strict/non-strict off-by-one |
| `src/broadcasters.py` | Drop `@retry_with_backoff` from `post_to_bluesky` and `post_to_mastodon`; add `_send_post_with_retry` to Bluesky; swap Mastodon's inner sleep for `sleep_for_rate_limit`/`sleep_for_transient`; return `BroadcastResult`; emit `bluesky_partial_delivery` on exhaustion |
| `main.py` | `BroadcastPayload` gains `bsky_sent_uris`, `mastodon_sent_ids`; `broadcasting_stage` unpacks `BroadcastResult` instead of treating `results[0]` as a client |
| `tests/test_retry.py` | Existing 5 tests stay valid (now testing the helpers); add cases for `classify_retry` classification boundaries |
| `tests/test_broadcasters_partial.py` — **new** | Simulate mid-thread failure on both platforms; verify earlier posts are **not** re-sent and `*_partial_delivery` is logged; verify `BroadcastResult.sent_uris` is populated on partial |

---

## Risks

- **Partial-thread delivery becomes the explicit outcome on hard failures.** That's actually more honest than today's behaviour (silent duplicates on Mastodon, full-thread replay on Bluesky). The reader occasionally sees a 2-post stub where the bot intended a 3-post thread — worth ensuring the Actions log makes this obvious.
- **Mid-thread auth expiry.** If a Bluesky session goes stale mid-thread, per-post retry can't re-auth — it'll exhaust the transient budget on 401s. `bluesky_partial_delivery` fires, run continues. Acceptable; rare; matches pre-plan behaviour since the outer retry also couldn't re-auth.
- **`update_profile_bio` and other decorated functions** stay as-is. This change is surgical to the thread-posting path.

---

## Non-goals

- **Resuming a partially-delivered thread on the next run.** Too much plumbing; not worth it. An aborted thread stays aborted; the next day's run posts fresh content.
- **Cross-platform atomicity** (either both platforms post the full thread or neither). Out of scope — `asyncio.gather` already lets one platform succeed while the other fails; that stays.

---

## Effort

~2.5h including tests (was ~2h; the Mastodon inner-retry extension and the `BroadcastResult` threading through `main.py` add ~30 min). Small, focused change — but bigger than the original sketch implied.

**Strong recommendation: execute alongside `PLAN_engagement_feedback.md` Slice A.** Slice A needs `sent_uris` out of the broadcasters anyway. Doing them together turns one signature change into two features; doing them apart means editing `broadcasters.py` and `main.py` twice.
