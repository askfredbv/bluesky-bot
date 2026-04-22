# Plan: Per-post idempotency for thread broadcasts

## Why this exists

`retry_with_backoff` currently wraps the *whole* `post_to_bluesky` call. If post 3 of a 5-post thread triggers a 429 or network error, the retry replays the entire function — **re-sending posts 1 and 2 as duplicates.** Same flaw on the Mastodon side (`post_to_mastodon` is also wrapped at the outer level despite having per-post retries internally).

The 429 handling shipped in v4.9.1 acknowledged this as a known limitation. It stays a limitation today.

**Trigger to execute this plan:** a duplicate post actually appears in production. Until that happens this is premature optimisation — threads are mostly 1–2 posts now (v4.14 single-post default), and rate limits haven't materialised. Noted explicitly to avoid the "we should fix this eventually" loop.

---

## Approach

The fix is architectural, not additive. Move retry decisions **inside** the broadcaster functions at the per-post granularity, and drop the outer `@retry_with_backoff` wrapper that's currently causing the re-send.

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
async def post_to_bluesky(client, posts, ...):
    sent = []
    for i, post in enumerate(posts):
        try:
            uri = await _send_with_retry(client, post, reply_to=sent[-1] if sent else None)
            sent.append(uri)
        except RetryExhausted:
            SafeLogger.warn("thread_partial_delivery",
                            posted=len(sent), expected=len(posts), stopped_at=i)
            break                # ← stop; don't rewind; honest partial success
    return sent
```

Posts already on the wire stay there. Failures stop the thread cleanly with an explicit `thread_partial_delivery` log event.

### The per-post retry helper

Extract the retry-decision logic from `retry_with_backoff` (in `src/utils.py`) into a helper that's callable per-post rather than as a decorator:

```python
async def backoff_and_retry(attempt: int, rate_limit_attempt: int, exception) -> tuple[int, int]:
    """Inspect the exception, sleep the appropriate duration, return updated counters.

    Raises the exception if both retry budgets are exhausted.
    """
```

`retry_with_backoff` stays as a thin decorator that uses the helper — existing call sites (non-threaded async calls like `get_link_metadata`, `mastodon.media_post`) keep their semantics unchanged.

---

## Files to change

| File | Change |
|---|---|
| `src/utils.py` | Extract `backoff_and_retry()` helper; refactor `retry_with_backoff` to call it |
| `src/broadcasters.py` | Drop `@retry_with_backoff` from `post_to_bluesky` and `post_to_mastodon`; add per-post retry loops that use `backoff_and_retry()`; emit `thread_partial_delivery` on exhaustion |
| `tests/test_retry.py` | Existing 5 tests stay valid (now testing the helper) |
| `tests/test_broadcasters_partial.py` — **new** | Simulate mid-thread failure; verify earlier posts are **not** re-sent and `thread_partial_delivery` is logged |

---

## Risks

- **Partial-thread delivery becomes the explicit outcome on hard failures.** That's actually more honest than today's behaviour (silent duplicates). But it does mean the reader sometimes sees a 2-post stub where the bot intended a 3-post thread — worth ensuring the logging in Actions makes this obvious so you notice.
- **Mastodon `post_to_mastodon` already has an inner `_status_post_with_timeout_and_retry`.** Removing the outer `@retry_with_backoff` is safe there because the inner retry handles transient errors. Need to verify the inner retry doesn't mask 429s though — currently it treats all failures the same way.
- **`update_profile_bio_mastodon` and other decorated functions** stay as they are. This change is surgical to the thread-posting path.

---

## Non-goals

- **Resuming a partially-delivered thread on the next run.** Too much plumbing; not worth it. An aborted thread stays aborted; the next day's run posts fresh content.
- **Cross-platform atomicity** (either both platforms post the full thread or neither). Out of scope — `asyncio.gather` already lets one platform succeed while the other fails; that stays.

---

## Effort

~2h including tests. Small, focused change.
