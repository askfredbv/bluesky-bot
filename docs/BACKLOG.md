# Backlog

Living list of pending work and parked ideas. Bot is shipping at v4.20.1 — the model chain (gemini-2.5-pro primary, gemini-2.5-flash fallback) is producing genuinely sharp Frederik-voice content, the 2026-05-15 batch broadened the RSS diet beyond AI-only, sharpened the Mentor topic pool, and dialled Pioneer to 0.35, and the v4.20.1 patch fixed the bluesky_session_stale revocation loop. Nothing below is urgent.

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

### Shipped 2026-05-08 → v4.19.0

Nine commits (`8c99378` … `27a1b1f`) landed Option 1 work and were validated in production over the following days: `gemini-2.5-pro` as primary model, `growth.json` follower-count snapshots, Curator "lead with the finding" prompt rewrite, Mentor topic pool 4 → 12, ruff CI integration. See the 2026-05-11 release notes for the substantiating live-feed read.

**Reading discipline (binding per retro):** pull the live feed via `app.bsky.feed.getAuthorFeed` and read the posts. Metrics are not a substitute for reading.

### Next after validation

1. **Phase 4b — proactive replies** [**in flight, 2/6 commits landed**]. The only audience-acquisition lever in the plan. Watchlist exists from Phase 4a (two handles: `simonwillison.net`, `xeiaso.net`). Multi-session arc — one commit per session is the working rhythm. **Shipped:** commit 1 in `0d04426` (state schema + I/O helpers, 12 tests); commit 2 in `639fa05` (`generate_proactive_reply` + SKIP contract + few-shot prompt, 17 tests). Daily run is unchanged. **Next commit:** `scan_watchlist` / filter logic / `pick_reply_candidate` / `stage_draft_reply` in `src/proactive.py` — the read-side scan flow with parent filtering (<12h age, ≥1 engagement, no quote/repost, no reader-bait parent, cooldown window respected). See `PLAN_engagement.md §4b` for the full map.
2. **Custom-feed outreach** (user action, async, ~1h). Identify 3–5 relevant AI/tech Bluesky custom feeds; ask curators to include `@askfred.be`.

~~Bluesky session cache fix~~ shipped 2026-05-15 in v4.20.1 (`76f1287`) — cache actually works now, was decorative before due to missed REFRESH events.
~~Reach data spike~~ shipped 2026-05-12 in v4.19 (`48333a9`) — Bluesky exposes `quoteCount` and `bookmarkCount`, not impressions; both now captured in `post_metrics.json`.

**Phase 2 (weekly digest) and Phase 3 (scoring multipliers)** remain data-gated and lower priority under Option 1. Building them before 4b ships is the trap the retro documented.

---

## §2 — Open issues (fix when convenient)

### Duplicate-source-posts follow-ups (surfaced 2026-05-13)

Three issues surfaced by the user's "duplicate-source posts" observation. The root cause (a Gist write 403 from a PAT missing `Gists: write` scope) was fixed end-to-end 2026-05-13 ~19:24 UTC — validated by `gh workflow run` showing zero `gist_state_save_failed` events. Items below are the follow-on work to prevent recurrence and clean up adjacent debt. **All three shipped:** #1 and #3 in `2be1148` (2026-05-14), #2 in `76f1287` (2026-05-15).

#### ~~1. Promote `gist_state_save_failed` from WARN to noisy / surfaced~~ [the retro callback] [**shipped 2026-05-14, `2be1148`**]

The 2026-04-22 retro flagged that silent state-persistence failures degrade duplicate-detection for days. The mitigation was never shipped, and the exact anti-pattern recurred 2026-05-11 → 2026-05-13: PAT regenerated without `Gists: write` → `_save_gist_state` returned 403 silently → state vanished between runs → duplicate-source posts on consecutive days. Found from the live feed, not from any alarm.

Shipped: log level promoted WARN → ERROR in `_save_gist_state` (surfaces in Actions UI same as `feed_health_record_failed`), and a new "Gist write smoke test" step in `daily_post.yml` does a real PATCH round-trip with `raise_for_status()` — fails the workflow loudly before the broadcast attempt if the PAT loses Gists:Write again. The original retro plan of surfacing `gist_state_save_failed` count in a weekly digest still follows from Phase 2 whenever that lands.

**Lesson worth keeping:** the same silent-degradation pattern recurred 3 weeks after the retro that documented it. Writing the retro is not the same as shipping the mitigation.

#### ~~2. `bluesky_session_stale` — token revocation loop, not expiry~~ [diagnosed 2026-05-13] [**shipped 2026-05-15, `76f1287`**]

The diagnostic shipped 2026-05-12 (`error_msg=str(e)[:200]` on the session_stale catch) surfaced: `error_type=BadRequestError`, `error_msg=Response(..., content=XrpcError(error='ExpiredToken', message='Token has been revoked'), ...)`.

Three hypotheses were on the table (password-login revokes prior, parallel manual logins, short refresh-JWT TTL). The real cause was none of them: **atproto's HTTP layer auto-rotates the JWT pair during the run** when the access token nears expiry. Each rotation invalidates the previous refresh_jwt server-side. Pre-fix, we only called `export_session_string()` once after password login — so the cache went stale *during* the run, and next run loaded the now-revoked refresh token. Not server-side revocation; **self-inflicted** by the bot's own normal API calls.

The atproto SDK has `Client.on_session_change` exactly for this, with the docstring tip: *"save the session string to persistent storage on SessionEvent.CREATE and SessionEvent.REFRESH event."* Pre-fix we handled CREATE (via the manual export after login) but not REFRESH. Now both fire through the same callback path; IMPORT is intentionally skipped (rewriting the same value would just noise the Gist patch history).

**Lesson worth keeping:** "revoked" in the error message was a red herring. From Bluesky's perspective, the previous JWT pair *was* revoked — but by the bot itself, via the SDK's normal refresh cycle. Three hypotheses on the table were all about external causes; the actual cause was the bot's own API traffic. The diagnostic message correctly named the symptom; reading the SDK source named the cause.

#### ~~3. `post_metrics_refreshed: errors=11` per run~~ [observed 2026-05-13] [**shipped 2026-05-14, `2be1148`**]

Validation run showed `bluesky=2, mastodon=0, skipped=2, errors=11` on the metrics refresh pass. 11 rows failing per run was noisy and — if the rows were genuinely unfixable — should prune rather than retry forever.

Shipped: Mastodon 404s now mark the row `orphaned=True` (upstream deletion) instead of counting as errors. `should_refresh` skips orphaned rows so the loop terminates. Detection uses exception type name or "404"/"Not Found" substring to avoid importing Mastodon.py classes into the metrics layer. Non-404 errors still count as errors. Four tests cover orphan-skip, 404→orphaned, non-404 counter, and no-repoll.

**Open follow-up:** the Bluesky-side hypothesis (URI format drift from older SDK versions) isn't addressed here. If `errors` stays >0 on next run after Mastodon 404s are filtered out, the remainder belongs to Bluesky and warrants its own diagnostic pass.

---

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
- **`google-genai` 2.x migration.** Currently pinned at `1.74.0` and shipping fine — `gemini-2.5-pro` is producing sharp content. Latest is `2.2.0`, but 1.x is still receiving releases (`1.75.0` exists alongside `2.2.0`), no Dependabot alerts open against the package, and no 2.x capability is on the immediate roadmap. Migrating now is the exact "infrastructure-over-output" trap the 2026-05-08 retro named. Triggers to revisit: (a) a run fails with a 1.x-specific SDK bug (diagnostic surface will catch it), (b) Dependabot files a CVE, (c) Phase 4b or another planned feature needs a 2.x-only capability. Until one fires, the pin stays.
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
