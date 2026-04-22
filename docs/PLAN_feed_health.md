# Plan: Feed health observability

## Why this exists

25 RSS feeds now, up from 17 in v4.12. Individual feeds go stale silently:
- A feed returns 200 OK but with 0 entries (CMS change, endpoint moved)
- A feed times out every run (SAX parse failures, read timeouts)
- A feed delivers content that's always >2 days old, so `fetch_single_feed`'s lookback filter drops everything

The Apr 22 run showed 4 feeds hitting `feed_parse_failure` (`deeplearning.ai`, `engineering.fb.com`, `stability.ai`, `anthropic.com/news.rss`) plus one `feed_timeout` (`the-decoder.com`). That's ~20% of the feed pool degraded on one run. Right now nothing surfaces this pattern across time — a feed could be dead for a month and the bot would keep pretending to consult it.

**Trigger to execute:** after `PLAN_engagement_feedback.md` Slice A is running. This plan piggybacks on that plan's state-capture pattern; doing it first would mean building the infrastructure twice.

---

## Approach

Two phases, both read-only — no changes to what the bot posts.

### Phase 1 — Capture (1h)

Extend `fetch_single_feed` in `src/utils.py` to return a structured result instead of just a list:

```python
@dataclass
class FeedFetchResult:
    url: str
    ok: bool                           # request succeeded (regardless of content)
    entries_total: int                 # raw entries in the feed
    entries_accepted: int              # survived lookback + normalisation
    error_type: Optional[str] = None   # e.g. "ReadTimeout", "SAXParseException"
```

`fetch_news` aggregates these into a `feed_health.json` Gist file (same Gist, same pattern as `post_metrics.json`). Shape:

```
{
  "feeds": {
    "https://...": {
      "last_fetch_at": "...",
      "last_ok_at": "...",
      "last_accepted_at": "...",      // last time it yielded an accepted item
      "recent_attempts": [
        {"at": "...", "ok": true, "accepted": 3, "error": null},
        ...                            // rolling 28 entries (~2 weeks, 2 runs/day)
      ]
    }
  }
}
```

### Phase 2 — Surface (30 min, needs Slice B of engagement plan)

Slice B of `PLAN_engagement_feedback.md` adds a weekly digest workflow. Extend the digest with a **Feed health** section that flags any feed with:

- `last_accepted_at` more than 14 days ago, OR
- `recent_attempts` showing >50% `ok=false` in the last 14 days

Human reads the digest and either removes the feed from `RSS_FEEDS` or investigates.

---

## Files to change

| File | Change |
|---|---|
| `src/utils.py` | `fetch_single_feed` returns `FeedFetchResult`; `fetch_news` records results |
| `src/metrics.py` (created in engagement plan Slice A) | Add `record_feed_health()`, `load_feed_health()`, `save_feed_health()` |
| `scripts/digest.py` (created in Slice B) | Add feed-health section |
| `tests/test_feed_health.py` — **new** | Shape tests for the new result type; rolling-window pruning; digest flagging logic |

---

## Non-goals

- **Automatic feed removal.** The digest flags problems; a human decides. Auto-removing feeds on a transient outage is worse than a month of dead-feed noise.
- **Per-feed retry logic.** `fetch_single_feed` already swallows errors gracefully. Adding retries there would slow every run and mask the signal this plan is trying to surface.
- **Monitoring via a third-party service.** All observability stays in the Gist + Actions logs. No new dependencies.

---

## Effort

~1.5h total (1h capture + 30 min digest extension). Cannot happen before Slice A of the engagement plan — they share infrastructure.
