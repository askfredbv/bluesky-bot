# Backlog

Living list of pending work and parked ideas. Bot is shipping fine at v4.17.1. Nothing here is urgent — the ordering below is what I'd tackle in sequence if I had the time.

---

## Priority order

1. **Wait for telemetry to accumulate** — Phase 2 unblocks ~2026-05-19 (see §1)
2. **Remaining open issues** — fix when convenient (see §2)
3. **Observational items** — wait for more runs, then decide (see §3)
4. **The plan** — `PLAN_engagement.md` covers everything else (see §4)

Post-length hard enforcement **shipped in v4.15.3** (2026-04-22) — see §2 for the retro.

---

## §1 — Wait state

Phase 1 (Steps 1–5) shipped and **production-confirmed 2026-05-05** across three consecutive runs with zero errors. Released as v4.17.0. There is no actionable Phase 1 work left.

What unblocks next, and when:

- **Phase 2 (weekly digest, ~1h)** — needs 2+ weeks of `post_metrics.json` data to produce signal worth reading. Earliest realistic: ~2026-05-19. Trigger: after that date, draft `scripts/digest.py` + `.github/workflows/engagement-digest.yml` per `PLAN_engagement.md §Phase 2`.
- **Phase 4b (MVP replies, ~4h)** — gated on Phase 1 data informing reply-prompt design *and* a fresh review of the watchlist (the 4a output `docs/WATCHLIST_AUDIT.md` is a regenerable artefact; rerun `python -m scripts.audit_watchlist` before starting 4b).
- **Phase 3 (scoring multipliers, ~3h)** — needs 4+ weeks of data. Earliest realistic: ~2026-06-02.

The right thing to do during the wait is **read the data as it accumulates**, not write more code. Specifically: glance at `post_metrics.json` and `feed_health.json` once a week, look for outliers (post types with consistently high/low engagement, feeds gone quiet), and decide whether the candidate Phase 2 digest sections (top/bottom posts, per-source averages, per-topic averages, pioneer category averages, strategist-fallback frequency, feed health) actually surface useful signal.

---

## §2 — Open issues (fix when convenient)

### ~~Profile bios drift from what the bot actually does~~ [**resolved 2026-04-29**]

Both bios manually pasted into the platform UIs. Config holds the canonical text in `APPROVED_BIO_BSKY` / `APPROVED_BIO_MASTODON` as reference for the next change. Voice now matches the bot's own posts: dry, statement-led, "house rules" line earns the dryness with a position rather than just enacting brevity.

The half-implemented automation (broadcaster fns + cooldown helpers) was removed in the same commit — bios change ~quarterly, manual paste is the right shape for that frequency.

Original issues:
- Wrong time (08:00 → 07:00 UTC) — fixed
- Strategist mode invisible — replaced with "house rules" framing that covers all three modes implicitly
- Slogan voice clashing with dry posts — replaced with `askfred.be in feed form. … LLM-written, house rules: no hype, no reader-bait.`

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

### ~~Broken promises — "more to follow", "more soon", "stay tuned"~~ [**resolved 2026-05-05**]

Shipped same-day as observation. New `BANNED_TEASER_PATTERNS` list in `src/config.py` covers `more to follow / more soon / more to come / stay tuned / to be continued / watch this space / follow for more / details coming / i'll dig deeper / i'll write more / i'll share more / thread incoming / 🧵`. Defensive trim in `agents.py` (`_ends_with_teaser`, `_strip_trailing_teaser`) handles both sentence-boundary and em-dash-fragment shapes — the live observation was "Notes on X — more soon." which is the em-dash fragment case. Prompt rules updated in `STYLE_GUIDELINES`. 10 new tests in `test_voice_trim.py`. Tonight's afternoon Mentor run is the first natural validator.

### Extend `post_metrics.json` schema with formatting features [observed 2026-05-05]

Phase 1 telemetry currently captures `had_image` and `had_link_card`, plus the raw `content_preview`. To answer formatting questions with data ("does length matter?", "do hashtags help?", "do questions in posts hurt?"), the schema needs a few cheap derived fields. Compute once at record time, no API calls:

- `emoji_count` — `re.findall` against a unicode emoji range, or use `emoji` package (already in deps? check)
- `hashtag_count` — `len(re.findall(r"#\w+", text))`
- `question_count` — count of `?` characters
- `length_chars` — `len(content_preview)` (or full post text — preview is currently capped at 80)
- `thread_length_posts` — already implicit via `thread_position`, but a denormalised count per row makes per-thread aggregation cheaper
- `time_of_day_bucket` — `"morning" | "afternoon"` derived from `posted_at` UTC hour

This is a Track A move per the formatting-→-engagement roadmap (see §3 "Voice formatting A/B"). Pure measurement enrichment — does NOT change what the bot writes. Goal: when Phase 2's digest design starts, the data already has the breakdowns it needs.

Effort: ~30 min in `record_post_metric` + `tests/test_metrics.py`. Risk: none (additive fields, ignored by older readers). Trigger: any time. Recommended to ship before Phase 2 starts so the digest doesn't have to backfill.

### Image reliability — Imagen 3 keeps failing [observed 2026-05-05, partially diagnosed]

`image_generation_failed` fires when the bot rolls into the image-generation path (`IMAGE_GENERATION_PROBABILITY = 0.5` of Mentor / Strategist runs). Across the last 7 afternoon Mentor runs, 2 attempted image generation and both failed with `error_type=ClientError` — the other 5 were dice-skipped, not silently succeeding. Posts that should have had an image went out without one.

**2026-05-05:** added `error_msg=str(exc)[:200]` to the failure log so the next attempt tells us *what* ClientError actually means. Same pattern as the 2026-04-29 Step 2 KeyError lesson — `error_type` alone is rarely enough to act on. Tonight's afternoon Mentor run (or whichever afternoon next rolls into image gen) is the diagnostic.

Once the message is in hand, the fix follows from what it says:

1. **Quota exceeded** → bump quota or move to a different account / billing tier.
2. **Model deprecated** (`imagen-3.0-generate-002`) → switch to current Imagen — likely a one-line config change.
3. **Content filter rejection** → add a guarded retry with a sanitised prompt, or a static fallback template (already exists for prompt-craft failure; extend to generation failure too).
4. **Auth / API key issue** → rotate the GEMINI_API_KEY; possibly the same key needs a separate Imagen entitlement.

Effort once diagnosed: ~30min – 2h depending on cause. Risk: low. Trigger: as soon as the next failure log lands with the message body.

---

## §3 — Observational (wait for data)

- **The Register main feed drift.** `https://www.theregister.com/headlines.atom` added in v4.13 alongside the software-specific feed. If Curator runs start surfacing space/security-humour content that isn't AI/tech dev-relevant, remove it from `RSS_FEEDS`. The `/software/headlines.atom` feed stays either way.
- **`CONSENSUS_SYNERGY_BONUS` retune.** Currently `1.5` per additional feed. With 25 feeds, a viral story covered by 5+ sources gets `+6.0` on top of its base score — could start dominating every Curator run. Drop to `1.2` if the Curator starts repeatedly picking the same wire-story everyone covers over genuinely distinctive items.
- **v4.16 slim refactor.** When `src/utils.py` (923 lines) or `src/config.py` (525 lines) grows another ~100 lines, do the split-into-focused-modules refactor before the next feature. Target layout: `state_io.py`, `feeds.py`, `scoring.py`, `url_safety.py`, `retry.py`, `image_io.py`; `config.py` keeps tunable constants only, prompt text moves to `prompts.py`, curated data to `src/data/{pioneer,feeds,topics}.py`. Mechanical; ~3h with tests. No plan doc needed — when the trigger fires, the layout above is the plan.
- **Audit script Mastodon path.** `scripts/audit_watchlist.py` returns opaque HTML errors when the Mastodon side fails. **2026-05-05**: added a `account_verify_credentials` preflight that runs once before iterating candidates and surfaces a clear message ("token belongs to @user" on success; "MASTODON_API_BASE_URL is wrong / token under-scoped" on failure with the curl command to verify). Root-cause fix is still in `.env` — the user has `MASTODON_API_BASE_URL=https://mastodon.social/@askfred` (a profile URL) where `https://mastodon.social` (the API base) is needed. Bot's posting still works in Actions because the GitHub secret has the right value; only local audit runs are affected. Revisit when adding new Mastodon-only candidates would benefit from automated scoring.
- **Voice formatting A/B — emojis, hashtags, length, questions.** Observation 2026-04-27 (re-raised 2026-05-05 as a roadmap question): scrolling the live feed, posts feel flat. The user's instinct is that occasional emojis, a topical hashtag, or other formatting touches might lift engagement. The strategic question is real; the implementation has to be careful because **voice is a brand decision, not a metric decision** (`PLAN_engagement.md` plan-wide non-goal). A reader returning to a feed that suddenly starts using emojis after 137 dry posts would notice the inconsistency.

  Three-track roadmap:

  - **Track A — instrumentation** (see §2 entry "Extend post_metrics.json schema with formatting features"). Cheap, no voice change. Captures emoji_count, hashtag_count, length, etc. so the data can actually answer "does X help" instead of guessing.
  - **Track B — image reliability** (see §2 entry "Image reliability — Imagen 3 keeps failing"). Highest-leverage structural fix that needs no voice change and no data — Imagen failures are a pure drag on the existing image-attach plan.
  - **Track C — voice formatting A/B** (this entry). Gated on (1) Track A having 4+ weeks of data, AND (2) explicit brand-direction approval that "we would use emojis even if data said they help." If both green: ship as A/B (50% of posts get format X, 50% don't), measure delta over 2+ weeks per format, ship the changes that survive the brand-coherence test as well as the engagement test.

  Levers to consider when Track C is in play: (a) ≤1 hashtag per thread when topic-anchored (no `#AI #tech #thoughts` listicle endings); (b) emoji policy "rare and load-bearing" — measurable at any non-zero rate currently means the rule isn't firing; (c) optional question hook on Mentor (different from the banned reader-bait patterns).

  **Do not skip ahead to Track C.** A blanket emoji change without instrumentation or A/B measurement is exactly the gut-feel "make it less dull" move the BACKLOG warned against on 2026-04-27 — and which the bio rewrite (2026-04-29) deliberately moved away from. The fact that the bios were de-emojified makes the question of "should posts get emojis" a coordinated brand decision, not an isolated experiment.

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
