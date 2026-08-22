# Plan: Engagement — measure, observe, act, participate

> **Status — 2026-08-19.** Phase 1 (capture) and Phase 4a (recon) are **shipped**; Phase 4b (proactive replies) is **code-complete but dormant** (human-gated, activated manually); Phases 2 (digest), 3 (scoring), and 4c (expansion) remain **parked** by choice. Current primary model is **`gemini-3.7-flash`** — the `gemini-2.5-pro` in the 2026-05-08 snapshot below is historical. Shipped-phase detail has been trimmed to a summary; the full original spec is in git history + `BACKLOG.md`.

> **Read [`RETRO_2026-05-08.md`](RETRO_2026-05-08.md) before executing further on this plan.** The phase ordering below (capture → digest → act → participate) was followed through 2026-04 / 2026-05 and shipped a coherent Phase 1, but the retro documents why the implicit assumption ("an audience exists to engage with the content") was wrong from the start. Phase 4 (proactive replies) is the only path in this plan that creates an audience; running 1→2→3 before 4 measured a feed that had no readers. Question whether the plan's phase order still matches the project's goal before adding work on top.

---

## GOAL CHANGE — 2026-05-08

The project's explicit goal is now **Option 1: build a following.** Real audience acquisition. See `RETRO_2026-05-08.md` for the framing decision. The phase ordering below was structurally tuned for an Option 3 (craft / learning) interpretation of the work; under Option 1 the ordering needs to be **re-read in priority order, not numerical order:**

| Phase | Original ordering | New priority under Option 1 |
|---|---|---|
| 4b. MVP proactive replies | "After 4a produces ranked watchlist" (data-gated) | **First in line.** Only audience-acquisition lever in this plan. Watchlist exists. Stop waiting for Phase 1 data — that gate was conservative-by-default. |
| Content-quality fixes (Curator template rewrite, Mentor topic pool, model swap to gemini-2.5-pro) | Not in plan; ad-hoc work | **Second priority.** Phase 4b will expose the feed to wider readership; output should be as good as we can make it cheaply before that lands. Model swap shipped 2026-05-08 (8c99378); the primary has since moved through gemini-3.5-flash to gemini-3.7-flash (see the status note at the top). |
| 1. Capture telemetry (Steps 1–5) | First | ✅ Shipped, production-confirmed v4.17.0. Still useful — informs Phase 2 + Track A formatting features feed it. Lower priority for new work now. |
| 4a. Recon (audit_watchlist) | "Anytime after Phase 1 ships" | ✅ Shipped, 2 stake-able candidates. |
| 2. Weekly digest | "After Phase 1 has run 2+ weeks" | **Lower priority under Option 1.** Useful when there's an audience to digest engagement from; less useful right now. Can still ship around ~2026-05-19 trigger, but not urgent. |
| 3. Scoring multipliers | "After 4+ weeks of Phase 1 data" | **Lower priority.** Same reasoning — wants an audience to measure against. |

**What this changes operationally:**

- Phase 4b's gate ("After 4a produces ranked watchlist") is **met**; the *implicit* gate ("Phase 1 producing data to inform reply-prompt design") was always loose and is hereby cleared.
- New work item not in original plan: **follower-count instrumentation**. Per-post engagement counts don't measure the Option 1 success metric (followers). Shipped 2026-05-08 alongside this rewrite as the cheapest possible parallel-track work during the model-swap validation window.
- The voice non-goal ("voice is a brand decision, not a metric decision") **still stands.** Option 1 changes what the bot *does* (replies, custom feeds, discoverability) — not what it *sounds like.*

---

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

## Phase 1 — Capture ✅ SHIPPED (v4.17.0, 2026-05)

Telemetry + delivery-safety, with zero change to what the bot posts. Every post writes a row to `post_metrics.json` (engagement + formatting features, refreshed ~daily, pruned >30d); every feed fetch records into `feed_health.json`; follower counts snapshot to `growth.json`. Partial thread deliveries stopped being silent — `@retry_with_backoff` was dropped from the broadcasters in favour of per-post retry with a per-thread shared budget and a `BroadcastResult` / `*_partial_delivery` contract. Landed as six shippable commits (retry-helper split → `FeedFetchResult`/feed_health → `BroadcastResult` → drop the decorator → metrics_context plumbing → refresh+prune), production-confirmed across three runs on 2026-05-04/05. Code lives in `src/metrics.py` plus `capture_post_metrics_stage` + `capture_follower_snapshot_stage` in `main.py`.

_The original ~190-line implementation spec + per-commit progress table lived here through 2026-08-19; it is preserved in git history and summarised in `BACKLOG.md`._

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

**4a acceptance gate met:** first audit ranked 6 candidates; pruning landed on **2 defensible Bluesky stake-a-reply matches** (both scored voice=100, reply-opportunity ≥58, aggregate >60). The named handles + scores live in the gitignored `docs/WATCHLIST_AUDIT.md` and `scripts/watchlist_candidates.py` (kept out of the public repo — see the 2026-06-15 "scrub targeting data" note below); they are not reproduced here on purpose.

Plan asks for "2–3 you'd stake a first reply on." The Bluesky-only result clears the lower bound. The plan's "top 5 defensible" framing assumed the seed list survived contact with reality; in practice 4 of 6 seeds were dead/squatted/political and got pruned. Slate is intentionally short until manual discovery adds more.

> **Targeting data scrubbed from the public repo (2026-06-15).** The repo is public; a committed list that names + scores specific people for reply-worthiness is needlessly exposing. So: the watchlist is loaded from the `PROACTIVE_REPLY_WATCHLIST` env var (set as a secret when activating 4b), the candidate-research list lives in the gitignored `scripts/watchlist_candidates.py` (template: `watchlist_candidates.py.example`), and the scored audit table lives in the gitignored `docs/WATCHLIST_AUDIT.md`. The machinery stays public as portfolio; the named targets do not. (Note: this scrubs the working tree, not git history — the names remain in past commits. Type B — the illustrative few-shot examples in `config.py` that attributed fabricated posts to real handles — was scrubbed the same way on 2026-07-16: those examples now use non-resolvable placeholder handles (`@a-tooling-dev.invalid` / `@a-systems-dev.invalid` — the `.invalid` TLD is RFC-reserved and can never be registered), so no fabricated posts are put in the mouths of named real accounts. Test fixtures that still use real handles (Type C) are a separate, lower-stakes matter, left as-is.)

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

**Candidate seed list:** kept in the gitignored `scripts/watchlist_candidates.py` (template: `watchlist_candidates.py.example`) — independent-practitioner accounts that post substantive observations, plus Dutch/Belgian tech folks via manual discovery. Not listed here (named-targets-out-of-public-repo, see the scrub note above).

**Hard exclusions:** frontier-lab CEOs (ambulance-chasing optics), AI-news aggregators (amplifies noise), political accounts of any stripe, hot-take accounts.

**4a success criterion (gate to 4b):** ranked table where top 5 handles are defensibly good matches, and you can point to 2–3 you'd stake a first reply on.

### 4b. Minimum viable reply loop (~4h, 2–3 handles, human approval)

Top 2–3 handles from 4a, hard-coded as `PROACTIVE_REPLY_WATCHLIST` in config. Daily scan picks **at most one** reply candidate. **Bot does not post the reply** — writes a draft to new Gist file `pending_replies.json`. Human approves/rejects via manual `workflow_dispatch`.

**Structural separation (load-bearing kill-switch — 2026-05-15):** the proactive pipeline lives in its own module (`src/proactive.py`), its own workflows (`proactive_scan.yml` + `approve_pending_reply.yml`), and its own state file (`pending_replies.json`). **Never tangle the daily Curator/Mentor path with it.** No imports from `main.py` into `src/proactive.py`. No shared state between `seen_articles.json` and `pending_replies.json`. No code path where disabling Phase 4b breaks the daily post. This is the kill-switch guarantee — if the feature turns out to be tone-deaf or off-brand, disable the workflows in the Actions UI and the bot returns to its pre-4b behaviour with zero risk to the Curator/Mentor pipeline. Each future commit on 4b must preserve this property; if a wiring change would require importing `proactive` into the daily flow, that's a design smell and the answer is to refactor the shared piece into `src/utils.py` instead.

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
                "parent_author": "example-dev.bsky.social", "parent_text": "...",
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
