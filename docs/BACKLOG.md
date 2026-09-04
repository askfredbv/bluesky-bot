# Backlog

Living list of pending work and parked ideas. Bot is shipping at **v4.25.0** (2026-09-04) — a news-coverage release. The bot had been missing obvious model launches, and the root cause was coverage: it had no direct line to the vendors. Removed the dead `www.anthropic.com/news.rss` (404 since the claude.com rebrand — it had been failing silently every run) and added five verified primary vendor feeds — Google AI, Google Gemini, Mistral, OpenAI developers and NVIDIA — each with a `SOURCE_TIERS` entry, so a launch now enters the pool first-hand at tier 10 instead of arriving hours later via a lower-tier aggregator. A **feed-health alert** surfaces the next silent feed death as two distinct time-based signals (`broken`: no successful fetch in 3 days; `stale`: fetches fine but no usable entry in 14 days), and a non-2xx response now correctly counts as a failed fetch rather than a success returning an error page. Consensus counts distinct feed hosts, so one publisher's overlapping category feeds no longer earn a cross-publisher bonus. `IMAGE_GENERATION_PROBABILITY` 1.0 → 0.85 on both generated-image paths for cadence variety. **Deliberately not shipped:** a "landmark" scoring gate meant to stop the −12 topic-diversity penalty burying a flagship story. It went eleven review rounds across two approaches (launch-language parsing, then publisher-consensus counting) without converging; both were proxies for "importance" inferred from headline text, which is an unbounded problem. Abandoned rather than iterated — see §3 for the replacement direction. The feed work above already addresses the original miss. **v4.23.0** (2026-08-29) bundled the work merged since v4.22.0, dominated by a “shake the tree” security-and-quality audit and a mutual cross-repo review with strike007-3000/BluBot. **Cross-repo learnings:** a process-wide Pillow decompression-bomb cap (#74); a monthly workflow that refreshes `MOMENTUM_PRODUCTS` from the live news cycle via Gemini and opens a PR (#75); and fuzzy cross-publisher consensus in relevance scoring — the same story under different URLs across independent publishers now boosts, capped so a wire story cannot dominate (#76). **Security hardening:** the Gist state snapshot now allowlists non-sensitive files, closing a path where the proactive-reply watchlist could leak into a public Actions artifact (#77); RSS feed fetches go through the same SSRF guard (public-IP validation, DNS pinning, per-hop redirect checks) as the metadata scraper (#78); every GitHub Action is SHA-pinned (#79); vulnerable transitive deps bumped — cryptography 46→50, atproto 0.0.65→0.0.71, pyasn1, soupsieve (#80); and fetched response bodies are streamed with a 5 MB cap and are compression-bomb-safe (#91). **Architecture:** the 1266-line `utils.py` grab-bag was split into `state_store` / `net_safety` / `retry` / `news` (156 lines left), killing the `utils`↔`metrics` circular import (#81–84); the Pioneer history corpus moved to `data/pioneers.json` (#94); dead constants removed (#85); and the posting mode is now a `StrEnum`, so a typo is an import error, not a silent fall-through (#86). **Tests/CI:** the coverage gate tightened 70→80%, mypy type-checking added and expanded to a whole-codebase gate (#87, #89/#90/#92/#93), dependencies migrated to `pyproject.toml` (#95), and a tautological ranking test rewritten to exercise real code. The only content-behaviour change is the fuzzy-consensus scoring signal (a small additive ranking bonus); everything else is hardening and structure. v4.22.0 (2026-08-19) promoted gemini-3.7-flash to primary after a live voice trial and bundled the image-model migration, the Phase 4b privacy scrub, and a Pioneer broadening. Phase 4b remains code-complete and dormant (workflows ship disabled; activation requires manual user steps). Nothing below is urgent.

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

1. **Phase 4b — proactive replies** [**code-complete, dormant**]. The only audience-acquisition lever in the plan. Watchlist exists from Phase 4a (two seeded handles, kept in the gitignored `scripts/watchlist_candidates.py` / loaded at runtime from the `PROACTIVE_REPLY_WATCHLIST` env var — named targets are out of the public repo as of 2026-06-15). **Shipped:** commit 1 `0d04426` (state schema + I/O); commit 2 `639fa05` (`generate_proactive_reply` + SKIP contract); commit 3 `f0484b6` (scan/filter/pick/stage); commit 4 `1f3585e` (scan workflow + entry point); commit 5 `6e1e838` (approve/reject + posting workflow — first commit where a reply can actually go live on Bluesky, via explicit `workflow_dispatch action=approve`). Daily run is unchanged; kill-switch verified intact across all five commits. **Both workflows ship dormant.** Production activation requires manual steps: enable both workflows in the Actions UI; add a cron-job.org trigger for `proactive_scan.yml` at ~10:00 UTC; manually fire `approve_pending_reply.yml` when you want to approve or reject a staged draft. Phase 4b is complete as code and dormant — the README notes the pipeline (`src/proactive.py`, "dormant; human-gated"), and the real-handle scrub since then also covered the few-shot examples and test fixtures (Type B/C, #63/#64). The 2-week production validators per `PLAN_engagement.md §4b` begin from the day the workflows are activated. See `PLAN_engagement.md §4b` for the full map.
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

### ~~Type-check foothold → whole-codebase mypy gate~~ [**resolved 2026-08-29**]

The 2026-08-29 audit added a mypy "foothold" (#87): the CI type-check step gated only the 7 already-clean modules (`config`, `retry`, `metrics`, `proactive`, `settings`, `logger`, `bluesky_session`), with `mypy` pinned in `requirements.in` (`>=2.3,<3`) so the gate can't silently drift on an unmanaged version bump. The remaining 8 modules carried 32 known mypy errors. Cleared incrementally, one small PR per module group, each verified (mypy clean + full pytest green + ruff) and squash-merged: #88 `state_store`/`net_safety`/`news`, #89 `utils`/`broadcasters`, #90 `agents`, #92 `main`/`file_lock`. The step is now the whole-codebase gate `mypy src/ main.py --ignore-missing-imports` — every module is clean, so any new type error fails the build.

Most of the 32 were over-narrow annotations: `seen_data` is heterogeneous → `Dict[str, Any]`; `PIL.Image.open` returns `ImageFile` but `.convert()`/`.resize()` return `Image.Image`; `get_with_safe_redirects`' `timeout` widened to httpx's `float | Timeout | None`. **Two were real bugs worth fixing properly:** (1) `main.py` unpacked `asyncio.gather(return_exceptions=True)` results with `isinstance(x, Exception)`, which misses `CancelledError` (a `BaseException`, not an `Exception`) — a cancelled broadcast task would slip the filter and crash on the `.client`/`.sent_uris` access; now guarded on `BaseException`. (2) `agents._sync_generate` / `_sync_generate_text` declared `-> str`, but the Gemini SDK types `.text` as `Optional[str]` (None on a content-filter refusal); normalised to the `""` sentinel callers already handle. `file_lock` switched its backend guard from `os.name` to `sys.platform` so mypy statically prunes the non-active platform's backend (it can't type `msvcrt` on POSIX or `fcntl` on Windows) — verified clean under both `--platform linux` and `--platform win32`. Behaviour-preserving except the `BaseException` guard, a strict safety improvement for the cancelled-task edge.

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

- **Semantic scoring pass — replaces the abandoned "landmark" gate.** The −12 topic-diversity penalty in `calculate_relevance_score` is magnitude-blind: it demotes a flagship launch simply because we posted on its topic the run before, which is how a real Gemini launch got buried. v4.25.0 fixed that from the *coverage* side (primary vendor feeds, so launches arrive first-hand at tier 10), but the penalty itself is untouched.

  **Do not rebuild an exemption gate.** Two attempts, ~11 review rounds, neither converged: (1) detect "this is a launch" from the headline with a regex — leaked on word order ("Acme launches a tool for migrating from GPT-5"), punctuation ("Introducing: GPT-5"), decimal versions ("Gemini 3.8" split on the dot), mid-word stems ("Unreleased GPT-5"), title/description bleed, short names ("o3" inside "o365"); (2) count distinct publishers covering the same flagship — leaked on headline variance (punchy titles don't cluster), alias/variant splitting ("GPT-5" vs "GPT 5"; Claude 4 / Opus 4 / Sonnet 4), and same-org domains (`openai.com` + `developers.openai.com`). Both are proxies for "importance" inferred from text, which is an unbounded problem: every fix revealed a new leak of the same kind. A second opinion (Gemini, 2026-09-04) reached the same conclusion independently, and also shot down the obvious "fix" of moving diversity to a selection-time score margin — that swaps one magic number for another on an uncalibrated scale, and removing the penalty from scoring lets the top-5 fill with variants of one story.

  **The direction instead**, when this is worth doing:
  - **Refuse** any regex for model names or importance keywords, and keyword-based consensus clustering. Dead ends, evidenced.
  - **Build** a cheap-LLM pass over the top ~15–20 candidates: classify topic, detect genuine launches, assign a 1–10 impact score. Fractions of a cent per run. The bot already calls Gemini to *write* posts; it just never calls anything to *judge* them. This deletes the regex/alias/domain-matching surface rather than patching it.
  - **Selection is probably the real lever, not scoring.** Only the top 5 reach the Curator, which is told to pick "the most interesting so-what" and can skip a top-ranked launch entirely. Consider handing it 7–10 items deduplicated across topics (MMR / slotting) and letting it choose — that also removes the need for a diversity penalty in the score at all.

  **Trigger:** worth doing if the live feed shows launches still being missed *after* v4.25.0's feed additions have had a few weeks. If coverage alone fixed it, this stays parked — it was always an optimisation, not the fix.

- **The Register main feed drift.** `https://www.theregister.com/headlines.atom` added in v4.13 alongside the software-specific feed. If Curator runs start surfacing space/security-humour content that isn't AI/tech dev-relevant, remove it from `RSS_FEEDS`. The `/software/headlines.atom` feed stays either way.
- **`CONSENSUS_SYNERGY_BONUS` retune.** Currently `1.5` per additional feed. With 25 feeds, a viral story covered by 5+ sources gets `+6.0` on top of its base score — could start dominating every Curator run. Drop to `1.2` if the Curator starts repeatedly picking the same wire-story everyone covers over genuinely distinctive items.
- **`google-genai` 2.x migration.** Currently pinned at `1.75.0` and shipping fine — `gemini-3.5-flash` is producing sharp content. Latest is `2.2.0`, but 1.x is still receiving releases (`1.75.0` exists alongside `2.2.0`), no Dependabot alerts open against the package, and no 2.x capability is on the immediate roadmap. Migrating now is the exact "infrastructure-over-output" trap the 2026-05-08 retro named. Triggers to revisit: (a) a run fails with a 1.x-specific SDK bug (diagnostic surface will catch it), (b) Dependabot files a CVE, (c) Phase 4b or another planned feature needs a 2.x-only capability. Until one fires, the pin stays.
- **Primary-model upgrade evaluation — RESOLVED 2026-08-19: KEEP `gemini-3.7-flash` as primary (promoted #68, validated on the live feed — the 08-19 Curator run read tight/first-person/on-brand). `gemini-3.5-flash` is the immediate fallback. Prior state — 2026-06-12: KEEP `gemini-3.5-flash` (superseded by this trial).** The Friday checkpoint pulled the live feed and read the 5 posts since the 2026-06-10 14:34 UTC cutover (2 Curator + 3 Mentor) against the two-register voice anchor. Verdict: 3.5-flash holds the voice — as sharp, terse, and first-person as 2.5-pro (e.g. Mentor "…It is a slow way to fail.", Curator "I keep seeing teams celebrate the speed of AI-generated code, but the structural bill is coming due"). The earlier n=1 "more florid" first sample (the 14:34 Mentor post) was an outlier, not a pattern. Strict win confirmed: faster + cheaper + a generation newer, voice intact, and `_thinking_budget_for()` already pins it to 0 so there is no empty-output risk. `GEMINI_MODEL_PRIORITY` comment in `config.py` updated to record the decision. **3.5-pro was explicitly NOT a target** (Pro-tier is overkill for this light twice-daily task — Frederik's call 2026-06-10); it would only have entered as a fallback-UP had flash read consistently flat. Historical evaluation record below for reference. **(finding 2026-06-09)** Prompted by comparing against a peer bot (`strike007-3000/BluBot`) that runs Gemini 3.1 Flash Lite primary. **Key insight — generation ≠ tier:** 3.1 Flash Lite is a *newer generation* but a *lower tier* than our `gemini-2.5-pro` (Flash Lite < Flash < Pro on writing quality within any generation). The peer optimised for speed + free-tier cost; we optimise for voice quality. So adopting his model would likely make our output *worse* on the axis we care about. The real question is whether a newer **Pro/Flash-tier** model beats 2.5 Pro on *writing* — and as of 2026-06-09 the lineup is known (Google's Gemini 3.5 announcement 2026-05-19 + deepmind.google benchmark table): **Gemini 3.5 Flash is GA via the Gemini API and beats 3.1 Pro on most agentic/coding/multimodal benchmarks; 3.5 Pro was rolling out "next month."** This UPGRADES the earlier "probably stay on 2.5-pro" lean — there is now a newer, generally-available model a full generation ahead of our 2.5-pro, and it is faster + cheaper (Flash tier). BUT the published benchmarks measure coding/agentic/reasoning/long-context — **none measure short-form writing or voice fidelity**, which is the only axis that matters here. So 3.5 Flash is "very capable", not proven "writes Frederik's voice better." **Must be evaluated empirically: run it once and read the feed, don't trust the benchmark.** Note: the `GEMINI_API_KEY` is **application-restricted** (IP/referrer) — from a laptop every call 403s "blocked" including 2.5-pro, but from the GitHub runners (allow-listed) everything works. So discovery must run via the `Model Discovery (diagnostic)` workflow (`.github/workflows/model-discovery.yml`, manual `workflow_dispatch`), NOT locally. **Authoritative result from that workflow (run 2026-06-09):** the key reaches 32 text models. **`gemini-3.5-flash` is GA (not preview), out=64k — the recommended test candidate** (newest generation, GA-stable, faster + cheaper than 2.5-pro, beats 3.1 Pro on Google's benchmarks). Pro-tier options are **preview-only** (`gemini-3-pro-preview`, `gemini-3.1-pro-preview`) — not ideal for an unattended daily bot; `gemini-3.5-pro` is **not yet reachable** by this key. **Switch procedure when ready:** (1) add `gemini-3.5-flash` handling to `_thinking_budget_for()` — return 0 (disable thinking, like 2.5-flash) so it doesn't hit the 2026-05-11 empty-output bug; (2) put it first in `GEMINI_MODEL_PRIORITY` for one Curator + one Mentor run; (3) READ the feed output vs 2.5-pro before keeping it. Rollback = one-line chain revert. (Minor: discover_models.py's `_generation()` only parses `X.Y`, so bare `gemini-3-pro-preview` (no minor) isn't auto-flagged as a candidate — cosmetic; the headline `gemini-3.5-flash` is flagged correctly.) Disciplined approach: (1) run `scripts/discover_models.py` with the live key to see what the account can actually reach today; (2) test the best Pro/Flash-tier 3.x candidate by putting it first in `GEMINI_MODEL_PRIORITY` for ONE run and *reading the feed output* (not benchmarks); (3) keep it only if it reads sharper than 2.5 Pro. **Two gotchas before switching:** (a) `_thinking_budget_for()` in `agents.py` only recognises `2.5-pro`/`2.5-flash` patterns — a 3.x model falls through to `return None`, sends no thinking_config, may use the default thinking budget and consume the whole output budget → the exact `AttributeError` bug from 2026-05-11; any new model needs its thinking-range pinned explicitly. (b) If a 3.x model requires the 2.x SDK, this couples to the parked **`google-genai` 2.x migration** item above — they become one task. **Free field data:** the peer runs 3.x in production and is doing the reverse comparison — ask him whether the 3.x *generation* reads sharper or flatter than what he ran before (his tier differs from ours, but the generational impression is useful). **Trigger:** when you feel like evaluating, or when a clearly-better Pro-tier 3.x model is confirmed available. No urgency — 2.5 Pro is producing sharp content today.
- **v4.16 slim refactor.** When `src/utils.py` (923 lines) or `src/config.py` (525 lines) grows another ~100 lines, do the split-into-focused-modules refactor before the next feature. Target layout: `state_io.py`, `feeds.py`, `scoring.py`, `url_safety.py`, `retry.py`, `image_io.py`; `config.py` keeps tunable constants only, prompt text moves to `prompts.py`, curated data to `src/data/{pioneer,feeds,topics}.py`. Mechanical; ~3h with tests. No plan doc needed — when the trigger fires, the layout above is the plan.
- **Mentor estimation-topic over-saturation — revisit if it recurs.** A snapshot on 2026-05-21 showed 4 estimation-variant posts in 25, suggesting the seed "estimating your own time vs estimating someone else's" was over-firing. On re-check 2026-05-24, post-rewrite data showed 1 estimation post in 2 Mentor runs — within expected range for a 12-seed pool with 5-slot dedup (`main.py:553`). The earlier saturation likely came from the pre-2026-05-15 pool where broad anchors like "Career" pulled toward estimation under multiple seeds. **Revisit if Mentor shows >25% estimation rate over a 2-week window.** Fix options when triggered: widen dedup window from 5 to 8, or split the estimation seed into two narrower seeds, or drop it temporarily. Don't act prophylactically — let the validators run.
- **~~`pyproject.toml` migration trigger~~ — DONE 2026-08-29.** Fired when the audit added `pytest-cov` + `mypy` alongside ruff/pytest/pytest-asyncio in `requirements.in`, mixing dev tooling with runtime deps that the daily production workflow then installed wholesale. **Resolved:** `requirements.in` replaced by `pyproject.toml` (runtime under `[project.dependencies]`, tooling under the `dev` optional-dependency group). Pinned reproducibility kept (Frederik's call over the "just `pip install .`" shape): two generated lockfiles — `requirements.txt` (runtime only, `pip-compile pyproject.toml`) and `requirements-dev.txt` (runtime + dev, `--extra dev`). Production workflows install `requirements.txt` (dev tools no longer land in production runs); CI installs `requirements-dev.txt`; `lockfile-check.yml` regenerates + diffs both. No behaviour change, no runtime-dep version change.
- **Lockfile-check vs Dependabot `--strip-extras` friction — durable fix when it gets annoying.** The `.github/workflows/lockfile-check.yml` workflow runs `pip-compile --strip-extras` and diffs the result against committed `requirements.txt`. Dependabot regenerates *without* `--strip-extras`, so it keeps proposing `google-auth[requests]` (with the extra) while the canonical CI output is `google-auth` (no extra) — every Dependabot PR's lockfile check fails until hand-corrected. This bit us 3 times (#46, #47, #49→#50); #50 (2026-06-09) fixed main itself (it had been red for 3+ weeks on this exact line) but did not fix the recurring friction. **Durable fix when the manual-correction tax gets annoying:** drop `--strip-extras` from the workflow so CI matches Dependabot's output. Needs a careful Linux `pip-compile` (NOT Windows — it injects win32-only `colorama`) to confirm no other extras leak in once stripping is off, then commit the regenerated lockfile. ~20 min on a Linux box or via a throwaway CI run. Until then: each Dependabot deps PR needs the `google-auth[requests]` → `google-auth` hand-edit (or supersede with a manual bump branch like #50). Not urgent — main is green; this is about reducing per-PR toil.
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
- 2026-08-29: **mypy foothold → whole-codebase gate.** All 32 known mypy errors across the 8 remaining modules cleared incrementally (#88–#92); CI now type-checks `src/ main.py` wholesale. Two real fixes surfaced (`main.py` gather `BaseException` guard; `agents.py` Gemini `.text` Optional contract); the rest were over-narrow annotations. Closed as a §2 resolved entry; the §3 `pyproject.toml` migration trigger is now fired (mypy + pytest-cov both landed).
- 2026-08-29: **`pyproject.toml` migration.** `requirements.in` → `pyproject.toml` (runtime deps + `dev` optional-dependency group). Pinned lockfiles kept: `requirements.txt` (runtime, production) + `requirements-dev.txt` (runtime + dev, CI), both generated from `pyproject.toml` by `lockfile-check.yml`. Dev tooling (ruff/pytest/mypy/pytest-cov) no longer installs into production runs. §3 trigger closed.
- 2026-08-31: **Request-level SDK timeout on every google-genai call (shutdown-hang fix).** Deferred from PR #101, where Codex noted the `asyncio.wait_for` around `generate_post_image` cancels only the awaiting coroutine, not the `asyncio.to_thread` worker — so a truly-hung synchronous call keeps its thread alive and `asyncio.run()` blocks in `shutdown_default_executor()` at exit, starving `daily_post.yml`'s post-run Gist snapshot. Fix: centralised client creation in `agents._get_client`, which sets `HttpOptions(timeout=IMAGE_GENERATION_TIMEOUT_SECONDS * 1000)` (ms) so the call itself raises and the thread finishes; covers text posts, image generation, and model discovery uniformly. `wait_for` kept as belt-and-suspenders (POST still ships without the image). Verified on the runner first (image-probe extended: generous per-request/client timeout still returns image bytes for the live `IMAGE_MODEL`; a 1 ms budget fails fast — proving the ms unit).
- 2026-08-31: **De-staled the image-probe "model is dead" notes (#103, doc-only).** The #102 probe run showed `gemini-3.1-flash-image` returning image bytes again (608 KB), contradicting the docstring in `scripts/probe_image_models.py` and the comment in `image-probe.yml`, which still said it "went unreachable ~2026-08-26". Both reframed as history (brief outage → reachable again by 2026-08-31) plus a standing diagnostic that also verifies the request-level `HttpOptions` timeout; the `# (now-dead) model` candidate comment corrected to `# the bot's live IMAGE_MODEL`. Comments/docstrings only — no behaviour change.
- 2026-08-31: **Release v4.24.0.** Cut from `main` after #98–#104. Bumps the version (README title, `main.py` startup banner, `pyproject.toml` `[project]`, BACKLOG header) v4.23.0 → v4.24.0. A visuals-and-resilience release: 100% image rate + Curator fallback image (#101), image telemetry + FORCE_MODE/FORCE_IMAGE hooks (#100), the request-level SDK timeout shutdown-hang fix (#102), the image-probe diagnostic workflow (#99), doc de-staling (#103/#104), and dependabot narrowed to security-updates-only (#98). No voice/content-shaping change beyond the version string; #101's 100%-image behaviour is the one runtime change and it landed earlier.
- 2026-09-03/04: **News coverage: primary-source feeds + feed-health alert + image rate 0.85.** Prompted by missing an obvious Google model launch. Root cause had two halves. **Half one, fixed here — coverage:** removed the dead `www.anthropic.com/news.rss` (404 since the claude.com rebrand, no usable replacement exists — it had been silently failing every run) and added verified-live primary vendor blogs (`blog.google/technology/ai`, `blog.google/products/gemini`, `mistral.ai`, `developers.openai.com`, `blogs.nvidia.com`) with matching `SOURCE_TIERS`, so a flagship launch enters the pool first-hand at tier 10 instead of arriving hours later via a lower-tier aggregator. **Feed-health alert:** `check_feed_health_alerts` warns on a *configured* feed that has died quietly, as **two distinct time-based signals**: **broken** (no successful fetch in `FEED_HEALTH_BROKEN_AFTER_DAYS`) and **stale** (fetches fine but no usable entry in `FEED_HEALTH_STALE_AFTER_DAYS`, catching a 404-served-as-HTML or a format change while tolerating a genuinely quiet publisher). Deliberately not merged into one metric: a single-metric version oscillated between false-positiving on quiet feeds and missing feeds whose entries never parse — different failure modes, different thresholds. Time-based rather than counted over recent attempts, so it does not depend on runs-per-day. Scoped to `RSS_FEEDS` so a removed feed's row cannot alert forever, and skips feeds with no successful fetch on record (no baseline). **Image:** `IMAGE_GENERATION_PROBABILITY` 1.0 → 0.85 for cadence variety. Gates images the bot *generates* — the Mentor/Strategist post image and the Curator fallback card image — on both paths (Curator is the morning mode; gating only the afternoon halved the intended rate). It deliberately does not strip an article's own OG thumbnail: that is the publisher's image and part of a normal link card. **Half two, deferred:** the magnitude-blind −12 topic-diversity penalty that buries a flagship story when we posted on its topic recently. A "landmark" gate to waive it went through ten Codex review rounds without converging — first as launch-language parsing (leaked on word order, punctuation, decimal versions, mid-word stems, field boundaries), then as publisher-consensus counting (blind to headline wording, then inflated by loose name matching, then split by aliases, then by same-org domains). Split out to its own PR rather than hold this release: see the `landmark-consensus-gate` branch. The feed work above already addresses the original miss from the coverage side.