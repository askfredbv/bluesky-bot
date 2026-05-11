# Backlog

Living list of pending work and parked ideas. Bot is shipping fine at v4.18.0. Nothing here is urgent — the ordering below is what I'd tackle in sequence if I had the time.

> **Before working on this project, read [`RETRO_2026-05-08.md`](RETRO_2026-05-08.md).** Six weeks of infrastructure landed on top of a hardcoded fallback that was shipping placeholder posts to production the whole time. The retro names the pattern (metrics-as-substitute-for-reading) and the corrective (open the live feed first). Don't repeat it.

---

## Priority order

1. **Wait for telemetry to accumulate** — Phase 2 unblocks ~2026-05-19 (see §1)
2. **Remaining open issues** — fix when convenient (see §2)
3. **Observational items** — wait for more runs, then decide (see §3)
4. **The plan** — `PLAN_engagement.md` covers everything else (see §4)

Post-length hard enforcement **shipped in v4.15.3** (2026-04-22) — see §2 for the retro.

---

## §1 — Goal change executed: Option 1 (build a following)

**2026-05-08:** explicit commitment to Option 1 over Options 2 (representation) or 3 (craft). See `RETRO_2026-05-08.md` for the framing decision and `PLAN_engagement.md`'s GOAL CHANGE block for the phase re-ordering.

### Shipped 2026-05-08 (awaiting production validation)

Nine commits since v4.18.0, all on `main`, all hitting production at the next runs:

- `8c99378` — `gemini-2.5-pro` promoted to primary model (+ `response_text` diagnostic on JSONDecodeError)
- `45f27ce` — Follower-count snapshots per run (`growth.json` — the real Option 1 success metric) + docs realigned to Option 1
- `2db0ec0` — Curator prompt: lead with the finding, not the source; explicit ban on "Notes on X" openers with BAD/GOOD examples
- `8f1cae4` — Mentor topic pool 4 → 12 (mix of broad anchors + specific observation territories)
- `2b762ff` — Retro doc `RETRO_2026-05-08.md` + pointers from BACKLOG / PLAN / project memory
- `8eb934e` — README "How it works" sync (5 gaps closed)
- `be7dbd9` — Ruff scan: 8 unused imports removed; 1 forward-ref documented
- `27a1b1f` — Ruff CI integration (`ruff.toml` + lint step in `tests.yml`)

### Tomorrow's validators

| Run | What it confirms |
|---|---|
| 07:00 UTC Curator | (1) `gemini-2.5-pro` produces sharper output, (2) "Notes on X" pattern is gone, (3) `growth.json` populates cleanly |
| 14:30 UTC Mentor | (1) model swap visible in output, (2) topic picked outside the original 4 (or one of the originals — both are valid), (3) prose is structurally different from the recent placeholder posts |

**Reading discipline (binding per retro):** pull the live feed via `app.bsky.feed.getAuthorFeed` and read the posts. Metrics are not a substitute for reading.

If clean: cut v4.19.0 with release notes covering all 9 commits. If not clean: read the live feed, diagnose specifically, fix specifically.

### Next after validation

1. **Phase 4b — proactive replies** (~4h, multi-session arc). The only audience-acquisition lever in the plan. Watchlist exists from Phase 4a. No real gates left.
2. **Bluesky session cache fix** (~30 min). Minor; cached session string rejected on next run.
3. **Reach data spike** (~15 min). Does Bluesky API expose impressions?
4. **Custom-feed outreach** (user action, async, ~1h). Identify 3–5 relevant AI/tech Bluesky custom feeds; ask curators to include `@askfred.be`.

**Phase 2 (weekly digest) and Phase 3 (scoring multipliers)** remain data-gated and lower priority under Option 1. Building them before 4b ships is the trap the retro documented.

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

### Bluesky session cache misses every run [observed 2026-05-05]

`bluesky_session_stale` (with `error_type: BadRequestError`) fires on basically every natural run, immediately followed by `bluesky_session_cached` after the password-fallback succeeds. The caching path in `src/bluesky_session.py` is wired correctly — load from Gist → try session-string login → fall back to password → save new string back. So the writes happen; the reads happen; but the *next* read produces a string atproto rejects.

Leading hypothesis: only the access JWT is in the cached string, and atproto access JWTs expire in ~2h. The 12h gap between runs guarantees the cache is dead by the time the next run reads it. The refresh-token piece needed to renew is missing or being dropped during export. `atproto.AsyncClient.export_session_string()` may return a bundle that needs to be deserialised into the matching `client.login(session_string=...)` shape — worth a 10-min check that the round-trip is symmetric.

Other less-likely candidates: the Gist write is succeeding but truncating; atproto's session-string format changed across SDK versions (we're on 0.0.65); some race between save and the next run's GIST sync.

Cost in steady state: a few seconds extra per run + one extra password-login round-trip. Minor — but the cache exists *to* avoid that, and right now the cache is decorative.

Effort: ~30 min — print the exported session string locally, verify it round-trips through `client.login(session_string=...)`, check whether atproto's docs note an export/import mismatch in this version. Risk: low (existing fallback chain catches any breakage). Trigger: any time — pure efficiency, no user-facing impact, can sit indefinitely.

### ~~Wire `ruff` into CI to catch dead imports + style drift~~ [**resolved 2026-05-08**]

Shipped same-day as the first scan. `ruff==0.15.12` added to `requirements.txt`; `ruff.toml` codifies the project-style decisions (E701 single-line guards allowed; tests get F401/E402/E702 ignored since test files cluster imports near related blocks and use setup-and-patch one-liners idiomatically); `.github/workflows/tests.yml` runs `ruff check src/ main.py scripts/ tests/` as a step before pytest. Both E402 violations in `src/agents.py` fixed by consolidating the two stray imports into the existing `from src.utils` line at the top of the file. CI now fails on new unused imports / undefined names; the same 8 unused imports won't accumulate again silently.

### ~~Model priority chain is one model deep in practice~~ [**partially shipped 2026-05-08**]

Re-framed under Option 1 as a quality question, not a reliability one (see strategy turn in the 2026-05-08 session). Commit `8c99378`:

1. ✅ **Added `gemini-2.5-pro` as the new primary**, demoted `gemini-2.5-flash` to fallback. Quality + resilience combined move. If 2.5-pro isn't available for the API key, `filter_available_models()` prunes it and the chain falls through cleanly.
2. ✅ **Added `response_text=response_text[:300]` + `error_msg=str(e)[:200]`** to the `content_generation_attempt_failed` log on JSON errors. Next failure tells us what flash is actually returning (commentary wrap? unwrapped object? partial JSON?).

Awaiting 2–3 production runs to evaluate whether the model was the constraint vs prompts.

### ~~Image reliability — Imagen 3 keeps failing~~ [**resolved 2026-05-08**]

Diagnosed and fixed same-day. Diagnostic surface from v4.17.1 caught the actual error message on the 2026-05-07 14:30 Mentor run: `"404 NOT_FOUND. models/imagen-3.0-generate-002 is not found for API version v1beta"`. Imagen 3 was shut down per Google's deprecation notice; the standard drop-in is `imagen-4.0-generate-001`. Config bumped, README updated.

The diagnostic discipline ("never log error_type alone, always include error_msg") paid for itself again — same lesson as 2026-04-29's Step 2 KeyError. Worth keeping as the project default.

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
