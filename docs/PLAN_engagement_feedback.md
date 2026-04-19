# Plan: Engagement feedback loop

## Why this exists

The bot posts into a void. It has no signal on which posts land and which flop. Every tuning decision we've made — source tiers, consensus synergy, topic pool, pioneer categories — is gut feel. With engagement data flowing in, the bot gets measurably better each week instead of just *different* each week.

This plan splits the work into three slices. **Slice A is the only one worth doing first** — it turns on the data pipeline. Slice B produces a weekly digest for manual tuning. Slice C (automatic scoring adjustments) should only happen *after* 4+ weeks of real data, because sample size matters more than cleverness here.

---

## Data model

New Gist file: `post_metrics.json`. Capped at 100 entries × 2 platforms. Pruned by age, not count (drop entries older than 30 days).

```
{
  "posts": [
    {
      "post_id": "at://did:plc:.../app.bsky.feed.post/...",
      "platform": "bluesky",            // "bluesky" | "mastodon"
      "mode": "curator",                // "curator" | "mentor" | "strategist"
      "language": "English",            // "English" | "Dutch"
      "posted_at": "2026-04-19T09:03:11Z",
      "content_preview": "first 80 chars...",

      // Context — whichever applies to this post
      "topic": "LLMs",                  // curator mode
      "source_domain": "openai.com",    // curator mode
      "pioneer_id": "sparck-jones-idf", // mentor w/ pioneer
      "persona_variant": "analyst",     // mode persona
      "had_image": false,
      "had_link_card": true,
      "thread_position": 0,             // index in thread; 0 = root

      "metrics": {
        "likes": 0,
        "reposts": 0,
        "replies": 0,
        "fetched_at": "2026-04-20T09:00:00Z"
      }
    }
  ]
}
```

---

## Slice A — Data collection (3h, no behaviour change)

Goal: every post writes a row into `post_metrics.json`, and each run refreshes metrics for rows 24h–30d old.

**Files to change:**

- `src/broadcasters.py` — `post_to_bluesky` and `post_to_mastodon` return `List[str]` of post IDs (currently they return the client and nothing respectively). Capture `response.uri` on Bluesky, `status['id']` on Mastodon.
- `main.py` — after `broadcasting_stage`, call a new `capture_post_metrics_stage` that writes rows for each post with the context already in `BroadcastPayload` (mode, language, topic, pioneer_entry, image_bytes presence, link_meta presence).
- `src/metrics.py` — **new module**:
  - `load_post_metrics()` / `save_post_metrics()` — Gist-backed, same pattern as `seen_articles`
  - `record_post_metric(...)` — append a new row
  - `refresh_stale_metrics(bsky_client, mastodon_client)` — fetches current counts for rows where `fetched_at` > 24h old AND `posted_at` < 30d old, updates in place
  - `prune_old_metrics()` — drops rows older than 30d
- `src/utils.py` — add `POST_METRICS_FILE` alongside the other state files; the Gist fallback pattern matches `seen_articles` exactly.
- `tests/test_metrics.py` — **new**: record/load/refresh/prune shape tests with mocked API clients.

**Platform APIs:**
- Bluesky: `client.app.bsky.feed.get_posts(uris=[...])` returns `likeCount`, `repostCount`, `replyCount` per post. Batch up to 25 URIs per call.
- Mastodon: `mastodon.status(id)` returns `favourites_count`, `reblogs_count`, `replies_count`. One call per post — fine at <100 posts total.

**Refresh cadence:** called once per run (before `broadcasting_stage`, while the clients are warm anyway). Only hits the API for stale rows, so steady-state is maybe 10–20 API calls per run across both platforms.

**Success criterion:** after two runs, `post_metrics.json` in the Gist has rows for all four posts (2 Bluesky + 2 Mastodon) with non-null metrics. No changes to what the bot posts.

---

## Slice B — Weekly digest (1h, observational only)

Goal: once a week, log a structured summary that a human reads and uses to tune config manually.

**Files to change:**
- `.github/workflows/engagement-digest.yml` — new workflow, runs Sundays 10:00 UTC. Calls a new `scripts/digest.py` entry point that reads `post_metrics.json` and emits the digest.
- `scripts/digest.py` — **new**: computes and prints (to Actions log):
  - Top 5 posts by `likes + 2*reposts + replies` over the last 14 days
  - Bottom 5 (same window)
  - Per-source average engagement (curator only)
  - Per-topic average (mentor/strategist)
  - Pioneer category averages

No scoring changes. The human reads the digest and edits `SOURCE_TIERS` / `SECONDARY_TOPICS` / `PIONEER_FACTS_UNDATED` by hand.

**Success criterion:** after two weeks, the digest shows "openai.com: 12 posts, avg 4.2 engagement; theregister.com: 8 posts, avg 0.8" — concrete numbers to act on.

---

## Slice C — Scoring feedback (3h, requires 4+ weeks of Slice A data)

**Do not start until Slice A has been running for at least a month.**

Goal: the bot reads its own history and adjusts scoring automatically.

**Proposed signals:**
- `SOURCE_ENGAGEMENT_MULTIPLIER` computed as `avg_engagement(domain) / avg_engagement(all)`, clamped to `[0.5, 2.0]`, applied on top of `SOURCE_TIERS` lookup
- Topic selection for Mentor: weighted random over `SECONDARY_TOPICS` using rolling engagement (topics never posted get baseline weight to avoid starvation)
- Pioneer: drop any entry with <average engagement across its last 3 appearances

**Files to change:**
- `src/metrics.py` — add `compute_source_multiplier()`, `topic_weights()`, `pioneer_health()`
- `src/utils.py` — `calculate_relevance_score` consults the multiplier
- `src/agents.py` — `select_pioneer_topic` and Mentor topic picker consult the weights
- Tests.

**Risks:**
- Feedback loop amplifies early variance. One viral post about a niche topic could cause the bot to hammer that topic for weeks. Mitigate with the clamp `[0.5, 2.0]` and a "baseline weight" floor on unseen topics.
- Engagement is a lagging indicator. A sudden drop in a source's quality won't be reflected for 2–3 weeks.

**Success criterion:** compared to the pre-Slice-C baseline, Curator picks and Mentor topic distribution visibly shift toward higher-engagement areas, and the weekly digest trends upward over 4 weeks.

---

## Non-goals

- **Engagement-chasing voice changes.** The voice rules (no hype, no reader-bait questions, dry) stay even if data shows questions get more replies. Voice is a brand decision, not a metric decision.
- **Per-platform content divergence.** Same content on both platforms — don't start optimising Bluesky posts differently from Mastodon posts based on engagement.
- **Public metrics dashboards.** The digest is a log line in Actions. No UI, no website.

---

## Rollback

Each slice is one commit. `git revert` and the previous behaviour returns — except `post_metrics.json` sitting in the Gist, which is harmless to leave.
