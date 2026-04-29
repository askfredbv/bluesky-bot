# Backlog

Living list of pending work and parked ideas. Bot is shipping fine at v4.15.3. Nothing here is urgent — the ordering below is what I'd tackle in sequence if I had the time.

---

## Priority order

1. **`PLAN_engagement.md` Phase 1** (capture metrics + feed health + per-post idempotency; see §1)
2. **Remaining open issues** — fix when convenient (see §2)
3. **Observational items** — wait for more runs, then decide (see §3)
4. **The plan** — `PLAN_engagement.md` covers everything else (see §4)

Post-length hard enforcement **shipped in v4.15.3** (2026-04-22) — see §2 for the retro. Phase 1 is now the clear next step.

---

## §1 — Next up

### `PLAN_engagement.md` Phase 1 — Capture (data + observability + idempotency)

See `docs/PLAN_engagement.md`. Phase 1 bundles three changes that share a broadcaster signature change (`BroadcastResult`):

- **Post metrics** — capture post IDs at broadcast, refresh likes/reposts/replies for posts 24h–30d old, write `post_metrics.json` to the Gist
- **Feed health** — `fetch_single_feed` returns `FeedFetchResult`; aggregated into `feed_health.json` in the Gist
- **Per-post idempotency** — drop `@retry_with_backoff` from broadcasters, add per-post retry, emit `*_partial_delivery` on exhaustion. Fixes the silent re-send bug on Mastodon

Effort: ~5h. Output: real data flowing + Mastodon's silent re-send pattern fixed. After two runs you can verify it works; after two weeks you have enough to run Phase 2 (digest) usefully.

**Status 2026-04-23:** Steps 1–3b shipped (commits `6d581fe`, `7eb02e9`, `0afa0b4`, `185aad4`). Per-thread shared retry budget + `*_partial_delivery` events now live; two `workflow_dispatch` runs clean. Checkpoint-gated — waiting for ≥1 natural production run before Steps 4 (metrics_context plumbing) and 5 (refresh + prune). `feed_health.json` acceptance still pending: validation runs so far were Mentor mode; needs one Curator run for all 25 feeds. See `PLAN_engagement.md §1f` for the tracker.

---

## §2 — Open issues (fix when convenient)

### Profile bios drift from what the bot actually does [observed 2026-04-29]

**Status:** config updated to dry first-person Draft A in commit `2d30689` (210 chars, both platforms share one string). Push to live profiles via `python -m scripts.push_bio` (added 2026-04-29). **Live profile push not yet automated** — see follow-up below. Original issues:

- **Wrong time:** Bsky bio says `Curation @ 08:00 UTC`, actual schedule is 07:00 UTC.
- **Strategist invisible:** both bios frame the bot as Curator + Mentor only. On low-news days the bot mostly produces Strategist content, which readers see but the bio doesn't acknowledge.
- **Voice mismatch (resolved in config):** bios use promotional slogan voice ("Technical Broadcasting Engine", "🚀 High-signal, low-noise automation", "work smarter, not harder", emoji-heavy). The bot's own posts are dry, first-person, no hype words, no emojis (per the BANNED_HYPE_WORDS / BANNED_QUESTION_PATTERNS rules). A reader clicking through from a post lands on a profile that sounds like a different bot. Either the posts should look more like the bio, or the bio should sound more like the posts — and per the voice non-goals in `PLAN_engagement.md`, posts don't change. So bios get rewritten in the bot's actual voice. Related to §3 "posts read dull": before chasing emoji/hashtag changes in posts, the bio is the cheaper place to test whether warming up the brand surface makes the feed feel less flat.

### ~~Snapshot Gist state step 404s~~ [**resolved 2026-04-22**]

Root cause was *not* a urllib quirk: `GIST_TOKEN` and `GIST_ID` were simply missing from the repo secrets. The main bot silently degraded (`_save_gist_state` catches all exceptions with just a warn log); the snapshot step failed loudly because it called `raise_for_status()` — which was actually the better signal.

Lessons:
- **Silent state-persistence failures are a worse outcome than noisy ones.** The warn-only path in `_save_gist_state` meant the bot ran for a week+ writing to `/tmp` with no one noticing. Phase 1's metrics capture should avoid repeating this pattern — surface `gist_state_save_failed` count in the weekly digest.
- Fine-grained PATs DO work for Gists — Account-scope permission, not Repository-scope.

Fixes landed: `67bf81f` (UA header, cargo-culted but harmless), `8a9e07a` (urllib→httpx, keeps the codepath aligned with the bot's own Gist access).

### Post length is a hard requirement [**shipped v4.15.3, 2026-04-22**]

Retro kept for reference — the anti-pattern is worth remembering.

A Mastodon post ended *mid-sentence* with "De uitdaging blijft echter om" — conclusion missing. That's a bot tell. A post that ends mid-thought halves the credibility of every post that ends well.

**Root cause:**
- No `max_output_tokens` in `_build_generate_kwargs` — Gemini defaults applied
- `_safe_truncate_post` in `agents.py` word-boundary-trimmed before validation
- `_split_and_constrain_posts` in `broadcasters.py` word-boundary-split anything still over-length

**Fix (shipped):**
1. Cap generation at `max_output_tokens=600` — the model physically cannot emit more than a 5-post × 300-char thread plus JSON overhead.
2. `_validate_thread_shape` hard-rejects overshoot (was warn-only) → triggers retry/fallback.
3. Broadcasters enforce the invariant at send time; over-length content → skip the platform, log `broadcast_invariant_violated`. Missing one run beats posting a bot tell.
4. Deleted `_safe_truncate_post` and `_split_and_constrain_posts` — if content arrives over-length, surface the upstream bug, don't paper over it.

**Explicit non-goal:** auto-splitting into threads as length recovery. Threading is an editorial choice by the model; genuine 2-post content should arrive as two complete-sentence posts from Gemini, not one blob chopped by us.

---

## §3 — Observational (wait for data)

- **The Register main feed drift.** `https://www.theregister.com/headlines.atom` added in v4.13 alongside the software-specific feed. If Curator runs start surfacing space/security-humour content that isn't AI/tech dev-relevant, remove it from `RSS_FEEDS`. The `/software/headlines.atom` feed stays either way.
- **`CONSENSUS_SYNERGY_BONUS` retune.** Currently `1.5` per additional feed. With 25 feeds, a viral story covered by 5+ sources gets `+6.0` on top of its base score — could start dominating every Curator run. Drop to `1.2` if the Curator starts repeatedly picking the same wire-story everyone covers over genuinely distinctive items.
- **v4.16 slim refactor.** When `src/utils.py` (923 lines) or `src/config.py` (525 lines) grows another ~100 lines, do the split-into-focused-modules refactor before the next feature. Target layout: `state_io.py`, `feeds.py`, `scoring.py`, `url_safety.py`, `retry.py`, `image_io.py`; `config.py` keeps tunable constants only, prompt text moves to `prompts.py`, curated data to `src/data/{pioneer,feeds,topics}.py`. Mechanical; ~3h with tests. No plan doc needed — when the trigger fires, the layout above is the plan.
- **Audit script Mastodon path.** `scripts/audit_watchlist.py` Mastodon side returns HTML 200 instead of JSON for `account_search` — almost certainly the `MASTODON_ACCESS_TOKEN` lacks `read:accounts` scope, OR `MASTODON_API_BASE_URL` doesn't match the instance the token was issued for. Verify with `curl -H "Authorization: Bearer $TOKEN" https://<instance>/api/v1/accounts/verify_credentials` (should return JSON, not HTML). Not blocking — Phase 4a closed on Bluesky-only with 2 stake-a-reply candidates. Revisit when adding new Mastodon-only candidates would benefit from automated scoring.
- **Posts read "dull" — visual/textual variety.** Observation 2026-04-27: scrolling the live feed, posts feel flat. No emojis, no hashtags, rarely a link in-thread, and image hit-rate is low (Imagen 3 fails often, Curator runs intentionally use link cards instead). Existing voice rules (no hype, no reader-bait questions, dry, first-person) stay — but "dry" doesn't have to mean visually featureless. Decide based on Phase 1 engagement data: if posts with images / link cards / occasional hashtags consistently outperform plain prose, encourage them in the prompt. Specific levers to consider when the data lands: (a) raise Imagen 3 success rate or pick a more reliable image source; (b) allow ≤1 hashtag per thread when topic-relevant (no `#AI #tech #thoughts` listicle endings); (c) make sure Curator link cards are rendering — verify `had_link_card` rate in `post_metrics.json`; (d) emoji policy stays "rare and load-bearing" but may not currently be firing at all — check actual rate in posted content. **Do not act before Phase 1 has 2+ weeks of data** — gut-feel "make it less dull" is exactly the kind of change that ends up making the bot sound like every other AI-news account.

All three decisions get trivial once §1 (engagement metrics) is running — no more guessing at "is the Register feed hurting?"; look at the engagement data.

---

## §4 — The plan

**`docs/PLAN_engagement.md`** is now the single plan covering everything that needs sequencing:

| Phase | What | Effort | Trigger |
|---|---|---|---|
| 1 | Capture: metrics + feed health + per-post idempotency | ~5h | **Now** (this is §1 above) |
| 2 | Surface: weekly digest (posts + feeds + strategist-fallback freq + partial-delivery counts) | ~1h | After Phase 1 has run 2+ weeks |
| 3 | Act: scoring multiplier, Mentor topic weighting, pioneer pruning | ~3h | After 4+ weeks of Phase 1 data |
| 4a | Recon: virtual-follow watchlist script | ~1.5h | Anytime after Phase 1 ships |
| 4b | MVP replies: 2–3 handles, human approval gate | ~4h | After 4a produces ranked watchlist |
| 4c | Expansion: 8–10 handles, gate maybe lifted on trusted handles | ongoing | After 2+ weeks of 4b approved-reply data |

Total path-to-finish: ~14.5h spread over 2–3 months of calendar time, gated by data accumulation. Each phase has explicit success criteria that gate the next.

---

## Rejected

- **Threads (Meta) as a broadcast target.** Rejected multiple times. Don't resurrect.

---

## Changelog

- 2026-04-22: Pioneer-dimension telemetry item removed from §5 — subsumed by engagement plan (post metrics already capture `pioneer_id` context, so pioneer-category performance falls out of the digest for free).
- 2026-04-22: Consolidated §5 future ideas into §4 plans. Promoted per-post idempotency and feed health into their own plan docs. Strategist-fallback frequency folded into the engagement plan's digest (free rider — `mode` is in the post_metrics schema).
- 2026-04-22: **Collapsed five plan files into one.** `PLAN_engagement.md` is now THE plan, with four phases covering metrics + feed health + per-post idempotency (Phase 1), weekly digest (Phase 2), scoring feedback (Phase 3), and proactive replies (Phase 4 a/b/c). Deleted: `PLAN_engagement_feedback.md`, `PLAN_per_post_idempotency.md`, `PLAN_feed_health.md`, `PLAN_proactive_replies.md`, `PLAN_v4.16_slim.md`. The v4.16 slim refactor moved to BACKLOG §3 as a one-liner with the layout inline (no plan doc needed for a mechanical file split). §5 removed entirely — the unified plan structure makes a "future ideas" bucket redundant; new ideas either fit into a phase or live in §3 until a trigger.
