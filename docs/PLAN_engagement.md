# Plan: Engagement — measure, observe, act, participate

The bot posts into a void. Every tuning decision since v4.0 — source tiers, consensus synergy, topic pool, pioneer categories, voice rules — is gut feel. The Mastodon broadcaster silently re-sends already-delivered posts on mid-thread failure. Twenty-five RSS feeds are scored without any signal that some have gone dead. And the bot only broadcasts; it doesn't participate.

This plan addresses all of that as one coherent body of work, because the underlying changes share infrastructure: a broadcaster signature change unlocks per-post idempotency *and* engagement metrics; a Gist-state pattern unlocks post metrics *and* feed health; a weekly digest workflow surfaces all three signal streams in one place; and the same engagement signal that informs scoring also gates which handles get proactive replies.

Four phases, executed in order. **Each phase has a hard gate before the next starts.** Skipping ahead is how feedback-loop projects end up amplifying noise.

---

## Plan-wide non-goals

- **Engagement-chasing voice changes.** Voice rules (no hype, no reader-bait questions, dry, first-person) stay even if data shows reader-bait gets more replies. Voice is a brand decision, not a metric decision.
- **Per-platform content divergence.** Same content on both platforms — don't start optimising Bluesky differently from Mastodon based on engagement.
- **Public metrics dashboards.** All observability lives in the Gist + Actions logs. No UI, no website, no third-party service.
- **Auto-removing dead feeds or low-engagement sources.** Signals surface; humans decide. A feed that stops yielding for two weeks may be a CMS migration, not a dead feed.
- **Cross-platform atomicity** for thread broadcasts (either both succeed or neither). `asyncio.gather` already lets one platform succeed while the other fails; that stays.

---

## Phase 1 — Capture (~5h, no behaviour change)

Goal: every post writes a row to a metrics file; every feed fetch records its outcome; partial thread deliveries stop being silent. Zero change to what the bot posts.

### 1a. Broadcaster signature change

Currently `post_to_bluesky` returns `client` (used by `handle_interactions` downstream); `post_to_mastodon` returns nothing. Both wrap the entire thread in `@retry_with_backoff`, which causes silent re-sends on mid-thread failure.

Replace with:

```python
@dataclass(frozen=True)
class BroadcastResult:
    client: Any                    # Bluesky: AsyncClient; Mastodon: None
    sent_uris: List[str]           # at://… for Bluesky; status IDs for Mastodon
    error: Optional[Exception]     # None on clean delivery; populated on partial
```

Drop `@retry_with_backoff` from both broadcasters. Move retry decisions inside, at per-post granularity. Posts already on the wire stay there; failures stop the thread cleanly with `bluesky_partial_delivery` / `mastodon_partial_delivery` (event names symmetric, both already prefixed by platform). `BroadcastResult.sent_uris` is populated whether delivery completed or stopped early.

**Retry budget is per-thread, not per-post** — the budget counters (rate-limit and transient) are initialised once when the broadcaster starts and shared across all posts in the thread. Otherwise a 5-post thread × 3 rate-limit retries = up to 45 min of sleeps on a sustained 429. Budget exhaustion stops the thread cleanly; the next run picks up where this one left off (seen-article state already handles idempotency at the article level).

### 1b. Retry helper split

Today `retry_with_backoff` mixes 429 logic and transient-error logic in one decorator. Extract into focused helpers callable from per-post loops:

```python
def classify_retry(e: Exception) -> Literal["rate_limit", "transient"]:
    """Duck-type inspect e.response.status_code."""

async def sleep_for_rate_limit(attempt, exception):
    """Honour header hints from either platform; fall back to
    RATE_LIMIT_BASE_WAIT_SECONDS * attempt. Raises if attempt > RATE_LIMIT_MAX_RETRIES.

    Header normalisation (both shapes handled):
    - Bluesky: `Retry-After` (seconds, integer)
    - Mastodon: `X-RateLimit-Reset` (unix timestamp, ISO-8601) — convert to delay-from-now
    """

async def sleep_for_transient(attempt):
    """BACKOFF_FACTOR ** attempt + jitter. Raises if attempt > MAX_API_RETRIES."""
```

`retry_with_backoff` stays as a thin decorator using these helpers — existing call sites (`get_link_metadata`, `update_profile_bio`, `mastodon.media_post`) keep their semantics. Mastodon's existing `_status_post_with_timeout_and_retry` swaps its hand-rolled `min(2^n, 5)` sleep for `sleep_for_rate_limit` / `sleep_for_transient` — gains 429 awareness it doesn't have today.

Also fix the existing strict/non-strict off-by-one between the two retry budgets. Current code mixes `> RATE_LIMIT_MAX_RETRIES` (rate-limit path allows 4 attempts) and `>= MAX_API_RETRIES` (transient path allows 3). **Pick strict-greater everywhere** → both paths get 4 attempts (1 initial + `MAX_..._RETRIES` retries), matching the constant naming.

### 1c. Post metrics

New Gist file `post_metrics.json`. Capped at 100 entries × 2 platforms; pruned by age (drop > 30 days), not count.

```json
{
  "posts": [
    {
      "post_id": "at://did:plc:.../app.bsky.feed.post/...",
      "platform": "bluesky",
      "mode": "curator",
      "language": "English",
      "posted_at": "2026-04-19T09:03:11Z",
      "content_preview": "first 80 chars...",
      "topic": "LLMs",
      "source_domain": "openai.com",
      "pioneer_id": null,
      "had_image": false,
      "had_link_card": true,
      "thread_position": 0,
      "metrics": {
        "likes": 0, "reposts": 0, "replies": 0,
        "fetched_at": "2026-04-20T09:00:00Z"
      }
    }
  ]
}
```

New `src/metrics.py`: `load_post_metrics()`, `save_post_metrics()`, `record_post_metric()`, `refresh_stale_metrics(bsky_client, mastodon_client)`, `prune_old_metrics()`. Same Gist-fallback pattern as `seen_articles`. New stage `capture_post_metrics_stage` in `main.py`, runs after `broadcasting_stage`, consumes `BroadcastResult.sent_uris`.

**Refresh policy:** `refresh_stale_metrics` runs once per run (clients warm) — only hits API for rows where `posted_at > 2h AND fetched_at > 20h AND posted_at < 30d`. The 2h floor skips hour-old posts (nothing interesting to measure yet); the 20h stale threshold means every post is refreshed once per day even with 2 runs/day. Steady-state ~10–20 API calls per run. Intentional dead zone: 0–2h engagement data is never captured (acceptable — Phase 2 digest is a 7-day view).

**BroadcastPayload gains `metrics_context: Dict[str, Any]`**, populated upstream (`content_prep_stage` + `broadcasting_stage`) with the keys the capture stage needs — none of which are currently plumbed end-to-end:

| Key | Source |
|---|---|
| `mode` | already on `BroadcastPayload` |
| `language` | `Settings.platform.language` (already available) |
| `topic` | `chosen_topic` — already on `BroadcastPayload` |
| `source_domain` | derived from `news_items[0]['link']` in `content_prep_stage` |
| `pioneer_id` | `pioneer_entry['id']` — already on `BroadcastPayload` |
| `had_image` | `image_bytes is not None` — **currently a local var in `broadcasting_stage`**; promote to `BroadcastPayload` or compute a bool upstream |
| `had_link_card` | `link_meta is not None` — **currently dropped between `ContentPrepPayload` and `BroadcastPayload`**; plumb through |

`content_preview`, `posted_at`, `thread_position`, `post_id` derive from the broadcast loop itself — no upstream plumbing needed.

Platform APIs:
- Bluesky: `client.app.bsky.feed.get_posts({"uris": [...]})` returns `likeCount` / `repostCount` / `replyCount` per post. Batch up to 25 URIs per call.
- Mastodon: `mastodon.status(id)` returns `favourites_count` / `reblogs_count` / `replies_count`. One call per post — fine at <100 posts.

### 1d. Feed health

`fetch_single_feed` in `src/utils.py` returns a structured result instead of just a list:

```python
@dataclass
class FeedFetchResult:
    url: str
    ok: bool                           # request succeeded (regardless of content)
    entries_total: int                 # raw entries in the feed
    entries_accepted: int              # survived lookback + normalisation
    error_type: Optional[str] = None   # e.g. "ReadTimeout", "SAXParseException"
```

`fetch_news` aggregates these into `feed_health.json` (same Gist, same pattern as `post_metrics.json`):

```json
{
  "feeds": {
    "https://...": {
      "last_fetch_at": "...",
      "last_ok_at": "...",
      "last_accepted_at": "...",
      "recent_attempts": [
        {"at": "...", "ok": true, "accepted": 3, "error": null}
      ]
    }
  }
}
```

`recent_attempts` rolls at 28 entries (~2 weeks at 2 runs/day).

### 1e. Files & tests

| File | Change |
|---|---|
| `src/utils.py` | Extract `classify_retry`, `sleep_for_rate_limit`, `sleep_for_transient`; refactor `retry_with_backoff`; `fetch_single_feed` returns `FeedFetchResult`; `fetch_news` aggregates feed-health |
| `src/broadcasters.py` | Drop `@retry_with_backoff`; per-post retry loops; return `BroadcastResult`; emit `*_partial_delivery` |
| `src/metrics.py` — **new** | Post-metrics + feed-health: load/save/record/refresh/prune |
| `main.py` | New `capture_post_metrics_stage`; `BroadcastPayload` gains `bsky_sent_uris`, `mastodon_sent_ids`; unpacks `BroadcastResult` |
| `tests/test_retry.py` | Existing 5 tests stay valid (now testing helpers); add `classify_retry` boundary cases |
| `tests/test_broadcasters_partial.py` — **new** | Mid-thread failure on both platforms; verify earlier posts not re-sent and `*_partial_delivery` is logged |
| `tests/test_metrics.py` — **new** | post_metrics + feed_health: shape tests, refresh, prune |

### 1f. Execution order — six shippable commits

Each step is a single commit that leaves main green. Ordered by dependency and risk — 3b is the only step with real behavioural change, and it's deliberately isolated so the preceding work de-risks it.

| # | Step | Scope | Time | Risk |
|---|---|---|---|---|
| 1 | **Retry helper split** (pure refactor) | `src/utils.py`, `tests/test_retry.py` | 45m | low |
| 2 | **FeedFetchResult + feed_health.json** | `src/utils.py`, `src/metrics.py` (new), `main.py`, `tests/test_metrics.py` (new) | 1h | low |
| 3a | **BroadcastResult return type** (keep `@retry_with_backoff`) | `src/broadcasters.py`, `main.py`, `tests/test_broadcasters.py` | 45m | low |
| 3b | **Drop `@retry_with_backoff` + per-post retry** | `src/broadcasters.py`, tests extended | 1.5h | **HIGH** |
| 4 | **metrics_context plumbing + record-on-broadcast** | `main.py`, `src/metrics.py` extended, `tests/test_metrics.py` | 1h | low |
| 5 | **Refresh stale metrics + prune** | `src/metrics.py` extended, `main.py`, `tests/test_metrics.py` | 45m | low |

**Checkpoint after 3b.** Stop and wait for at least one live production run before starting Step 4. 3b is the only step that changes what the bot actually does on the wire (retry semantics, partial-delivery handling) — every subsequent step builds on its return contract, so a regression caught after Step 5 is far more expensive to debug than one caught after 3b.

**Step-level acceptance:**
1. `pytest` green. No dispatch needed — existing callers get identical semantics (minus the off-by-one fix).
2. `pytest` green. After next run, `feed_health.json` shows entries for all 25 feeds. No behaviour change to posts.
3a. `pytest` green + one manual `workflow_dispatch` to confirm the unpack threads cleanly through `main.py`. `sent_uris` populated on success only at this step.
3b. `pytest` green + one manual `workflow_dispatch`. Watch log for unexpected `*_partial_delivery`. **Pause here.**
4. `pytest` green. After next run, `post_metrics.json` shows rows with zero `metrics` sub-objects (refresh hasn't run yet).
5. `pytest` green. After 2 runs ~12h apart, previous day's rows show non-zero live like/repost counts.

**Why 3a/3b split:** the `BroadcastResult` type change (3a) is mechanical — signature and unpack sites only. Dropping the decorator and writing per-thread retry state (3b) is where wire behaviour changes. Separating them means if a bug surfaces, git-bisect lands on the right commit immediately.

**Cumulative time:** ~5h45m, natural split across two sessions with the 3b checkpoint as the boundary.

### Progress — as of 2026-05-02

| # | Status | Commit | Notes |
|---|---|---|---|
| 1 | ✅ | `6d581fe` | Retry helpers extracted (`classify_retry`, `sleep_for_rate_limit`, `sleep_for_transient`) |
| 2 | ✅ | `7eb02e9` + `5f8e2e2` | `FeedFetchResult` + `feed_health.json`. Original `7eb02e9` shipped a latent KeyError: `_load_gist_state` passed `filename=` as a logging kwarg, which collides with Python's reserved `LogRecord.filename`. The except's own log call raised, propagating past load_feed_health → fetch_news → `feed_health_record_failed`. Identified via the 2026-04-29 diagnostic patch (`9b12bbe`); fixed `5f8e2e2` with both narrow rename (`state_file=`) and a wide guard in `SafeLogger._emit` that auto-prefixes any reserved-name kwargs with `x_`. Acceptance confirms tomorrow's Curator run. |
| 3a | ✅ | `0afa0b4` | `BroadcastResult` return type; decorator still in place |
| 3b | ✅ | `185aad4` | Dropped `@retry_with_backoff`; per-thread shared retry budget; `*_partial_delivery` on exhaustion. Two `workflow_dispatch` runs clean. Natural runs since: clean. |
| — | **CHECKPOINT cleared** | — | The 2026-04-29 morning Curator run cleared the broadcast-path checkpoint (no `*_partial_delivery`, no `rate_limit_hit`). Step 2's separate KeyError bug surfaced on the same run and was fixed in `5f8e2e2`. |
| 4 | ✅ | `ff00aa6` + `27cf504` | metrics_context plumbing + record-on-broadcast. `post_metrics.json` schema (post_id, platform, mode, posted_at, content_preview, topic, source_domain, pioneer_id, had_image, had_link_card, thread_position, zeroed metrics sub-object). New `capture_post_metrics_stage`. Test isolation fix (`27cf504`) gitignored state files and added a noop monkeypatch in the e2e tests so the new stage no longer leaks state to disk during pytest. |
| 5 | ⏳ | — | Refresh stale metrics + prune (~45m). Unblocks once tomorrow's 07:00 UTC Curator run shows `feed_health.json` populated for all 25 feeds *and* `post_metrics.json` shows rows with zeroed `metrics` sub-objects. |

Tests: 230 passing.

### Phase 1 success criteria (gate to Phase 2)

After two runs:
- `post_metrics.json` has rows for all 4 posts (2 Bluesky + 2 Mastodon) with non-null metrics
- `feed_health.json` has entries for all 25 RSS feeds
- A simulated mid-thread failure (test only) does not re-send earlier posts and logs `*_partial_delivery`
- Zero behaviour change in production output

---

## Phase 2 — Surface (~1h, observational only)

Goal: once a week, a structured digest a human reads in 5 minutes and uses to tune config manually.

`.github/workflows/engagement-digest.yml` — new workflow, Sundays 10:00 UTC. Calls `scripts/digest.py`. Output is a structured Actions log, not a posted artefact.

### Digest sections

1. **Top 5 / bottom 5 posts** (last 14 days) by `likes + 2*reposts + replies`
2. **Per-source average engagement** (Curator only)
3. **Per-topic average engagement** (Mentor / Strategist)
4. **Pioneer category averages** (free rider — `pioneer_id` is already in the schema)
5. **Strategist-fallback frequency** (free rider — `mode` is in the schema; if firing >1/week the news pipeline needs tuning, if <1/week the complexity earns its keep)
6. **Feed health** — flag any feed where `last_accepted_at` > 14 days OR `recent_attempts` shows >50% `ok=false` in the last 14 days
7. **Partial-delivery counts** — `*_partial_delivery` events grepped from Actions logs over the window. If non-zero on Bluesky, the per-post retry surfaced a real failure pattern; if non-zero on Mastodon, Phase 1's silent-re-send fix was overdue.

Human reads the digest and edits `SOURCE_TIERS` / `SECONDARY_TOPICS` / `PIONEER_FACTS_UNDATED` / `RSS_FEEDS` by hand.

### Phase 2 success criteria (gate to Phase 3)

After two weeks of digests:
- The digest contains concrete numbers — `openai.com: 12 posts, avg 4.2 engagement; theregister.com: 8 posts, avg 0.8`
- At least one config edit has happened based on what the digest showed (a feed removed, a topic dropped, a source tier nudged)
- 4+ weeks of `post_metrics.json` data accumulated before Phase 3 starts (sample size matters more than cleverness)

---

## Phase 3 — Act on signals (~3h, requires 4+ weeks of Phase 1 data)

Goal: the bot reads its own history and adjusts scoring automatically.

### Signals

- **`SOURCE_ENGAGEMENT_MULTIPLIER`** = `avg_engagement(domain) / avg_engagement(all)`, clamped to `[0.5, 2.0]`, applied on top of `SOURCE_TIERS` lookup
- **Mentor topic selection**: weighted random over `SECONDARY_TOPICS` using rolling engagement; topics never posted get a baseline weight to avoid starvation
- **Pioneer pruning**: drop any entry whose last 3 appearances all underperformed average

### Files

| File | Change |
|---|---|
| `src/metrics.py` | Add `compute_source_multiplier()`, `topic_weights()`, `pioneer_health()` |
| `src/utils.py` | `calculate_relevance_score` consults the multiplier |
| `src/agents.py` | `select_pioneer_topic` and Mentor topic picker consult the weights |

### Risks

- **Feedback amplifies early variance.** One viral post about a niche topic could cause the bot to hammer that topic for weeks. Mitigated by the `[0.5, 2.0]` clamp and baseline-weight floor on unseen topics.
- **Engagement is a lagging indicator.** A sudden quality drop in a source won't be reflected for 2–3 weeks. Acceptable; the digest still flags the live signal.

### Phase 3 success criteria (gate to Phase 4)

Compared to pre-Phase-3 baseline: Curator picks and Mentor topic distribution visibly shift toward higher-engagement areas, and the weekly digest trends upward over 4 weeks. If the bot starts hammering a single topic, the clamp/floor weren't enough — revert and reconsider before doing Phase 4.

---

## Phase 4 — Participate (proactive replies, multi-week sequenced rollout)

**Different risk profile than Phases 1–3.** Phases 1–3 only change what the bot writes; Phase 4 changes who it interacts with. One tone-deaf reply to the wrong account burns more trust than a month of good replies builds. The phase is sequenced internally to make that nearly impossible, with a human approval gate that doesn't come off until evidence accumulates.

### 4a. Recon — virtual follow (~1.5h, no reply code yet)

**Status 2026-04-28:** ✅ **complete.** `scripts/audit_watchlist.py` + `scripts/watchlist_candidates.py` + `tests/test_audit_watchlist.py` (29 tests, all green). Output destination: `docs/WATCHLIST_AUDIT.md` (gitignored — regenerate locally). Run: `python -m scripts.audit_watchlist` with the bot's env vars.

**4a acceptance gate met:** first audit ranked 6 candidates; pruning landed on **2 defensible Bluesky stake-a-reply matches**:

| Handle | Aggregate | Voice | Reply opp. | Cadence | Engagement |
|---|---:|---:|---:|---:|---:|
| `simonwillison.net` | 69.3 | 100 | 83 | 5.5/wk | 4.09/post |
| `xeiaso.net` | 62.2 | 100 | 58 | 4.0/wk | 2.88/post |

Plan asks for "2–3 you'd stake a first reply on." The Bluesky-only result clears the lower bound. The plan's "top 5 defensible" framing assumed the seed list survived contact with reality; in practice 4 of 6 seeds were dead/squatted/political and got pruned (see `scripts/watchlist_candidates.py` history). Slate is intentionally short until manual discovery adds more.

**Mastodon coverage: deferred as nice-to-have.** The audit's Mastodon path returns HTML instead of JSON for `account_search` — likely a token-scope or instance-URL mismatch (see `BACKLOG.md §3`). Not a 4a blocker because Bluesky already satisfies the gate; revisit when adding new candidates would benefit from cross-platform scoring.

`scripts/audit_watchlist.py` — one-shot, not in the daily pipeline. Takes a candidate handle list (file), fetches last ~10 posts each from Bluesky and Mastodon, scores each handle on:

| Field | Heuristic |
|---|---|
| Topic fit | % of posts matching `TOPIC_MAP` + `SECONDARY_TOPICS` + pioneer categories |
| Voice compatibility | Presence/absence of `BANNED_HYPE_WORDS`, `BANNED_QUESTION_PATTERNS`; first-person usage; average length |
| Reply opportunity rate | % posts that are statements/observations vs reposts/screenshots/bare links |
| Posting cadence | Posts per week (below ~3/week the feed is too quiet to watch) |
| Engagement substrate | Avg `replies + reposts` per post (high-likes/low-replies = broadcasts more than converses) |

Output: ranked markdown table + sample-post appendix so the scores can be eyeballed against reality.

**Candidate seed list** (curate freely):
- *Bluesky*: `simonwillison.net`, `jeremyhoward.bsky.social`, `swyx.io`, `jbhuang.bsky.social`, `pytorch.bsky.social`, `huggingface.bsky.social`, Dutch/Belgian tech journalists (manual discovery)
- *Mastodon*: `@simon@simonwillison.net`, `@glyph@mastodon.social`, `@inthehands@hachyderm.io`, `@b0rk@jvns.ca`, `@jwildeboer@social.wildeboer.net`, Belgian `mastodon.be` tech folks (manual discovery)

**Hard exclusions:** frontier-lab CEOs (ambulance-chasing optics), AI-news aggregators (amplifies noise), political accounts of any stripe, hot-take accounts.

**4a success criterion (gate to 4b):** ranked table where top 5 handles are defensibly good matches, and you can point to 2–3 you'd stake a first reply on.

### 4b. Minimum viable reply loop (~4h, 2–3 handles, human approval)

Top 2–3 handles from 4a, hard-coded as `PROACTIVE_REPLY_WATCHLIST` in config. Daily scan picks **at most one** reply candidate. **Bot does not post the reply** — writes a draft to new Gist file `pending_replies.json`. Human approves/rejects via manual `workflow_dispatch`.

**Files:**
- `src/config.py` — `PROACTIVE_REPLY_WATCHLIST`, `PROACTIVE_REPLY_PROMPT`
- `src/agents.py` — `generate_proactive_reply(parent_text, parent_author, context)` using same Gemini fallback chain
- `src/proactive.py` — **new**: `scan_watchlist()`, `pick_reply_candidate()`, `stage_draft_reply()`. Filters posts <12h with any replies/reposts (skip dead posts), skips quote-posts, skips parents matching `BANNED_QUESTION_PATTERNS` (don't chase reader-bait)
- `.github/workflows/proactive_scan.yml` — daily ~10:00 UTC, scans + stages, never posts
- `.github/workflows/approve_pending_reply.yml` — `workflow_dispatch`, posts the first staged entry or marks it rejected based on input

**Reply prompt — hard rules:**
- First-person, askfred voice anchors
- Must add information the parent doesn't already have (an adjacent fact, counter-example, link)
- Under 200 characters — conversational, not another broadcast
- **Return literal `"SKIP"` if nothing substantive to say.** Essential — most posts shouldn't get a reply
- Never agree-and-amplify without content; never end on a reader-bait question

**`pending_replies.json` shape:**
```json
{
  "pending":  [{"id": "uuid", "platform": "bluesky", "parent_post_uri": "at://...",
                "parent_author": "simonwillison.net", "parent_text": "...",
                "draft_reply": "...", "generated_at": "...", "expires_at": "+24h"}],
  "posted":   [/* kept 14d for traction analysis */],
  "rejected": [/* kept 14d to spot prompt drift */]
}
```

**4b success criterion (gate to 4c):** over 2 weeks, 10–14 drafts staged, ≥50% approved, and approved replies generate ≥1 meaningful back-and-forth from the parent author or a third party. If approval rate <30%, the prompt needs tuning before expansion.

### 4c. Expansion (only after 4b's evidence)

- Expand watchlist to top 8–10 handles
- If approved:rejected ratio is consistently >80% and no reply has required damage control, *consider* removing the approval gate **for handles you've built trust with** — keep it for any new handle added later
- Feed traction data back into 4a ranking: handles where replies got engagement weighted up; handles where replies got ignored weighted down or dropped

**Hard non-goal:** never go fully unattended across the whole watchlist. Cost of one bad reply to a journalist or a high-visibility account is too high to automate away.

### Phase 4 plan-wide non-goals

- Chasing viral posts for visibility — ambulance-chaser playbook
- Template / macro replies — every reply generated fresh against the parent
- Replies on the bot's own threads to simulate engagement — obvious and embarrassing if caught
- Following accounts as a growth tactic — bot doesn't follow anyone; recon uses public API only

---

## Rollback per phase

- **Phase 1**: each sub-phase is one commit. Revert and the prior behaviour returns; `post_metrics.json` and `feed_health.json` left in the Gist are harmless.
- **Phase 2**: digest is a workflow + script. Disable the workflow.
- **Phase 3**: scoring multipliers gated behind a feature flag in config; flip off to revert.
- **Phase 4a**: one-shot script, nothing to roll back.
- **Phase 4b**: revert commits + delete `pending_replies.json` from the Gist. Bot never posts unattended in 4b.
- **Phase 4c**: if unattended replies go wrong, flip the approval gate back on. Don't delete the watchlist — handles are still useful under the gate.

---

## Effort summary

| Phase | Effort | Trigger |
|---|---|---|
| Phase 1 | ~5h | **Now** — shared infra, biggest unblock |
| Phase 2 | ~1h | After Phase 1 has run for 2+ weeks |
| Phase 3 | ~3h | After 4+ weeks of Phase 1 data |
| Phase 4a | ~1.5h | Independent; can run anytime after Phase 1 ships |
| Phase 4b | ~4h | After 4a produces ranked watchlist |
| Phase 4c | ongoing | After 2+ weeks of 4b approved-reply data |

Total path-to-finish: ~14.5h spread over 2–3 months of calendar time, gated by data accumulation.
