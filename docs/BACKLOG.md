# Backlog

Living list of pending work and parked ideas. Bot is shipping at **v4.27.1** (2026-09-12). Release notes for every version live on the [GitHub releases page](https://github.com/askfredbv/bluesky-bot/releases) — this file does not duplicate them.

> **Before working on this project, read [`RETRO_2026-05-08.md`](RETRO_2026-05-08.md).** Six weeks of infrastructure landed on top of a hardcoded fallback that was shipping placeholder posts to production the whole time. The retro names the pattern (metrics-as-substitute-for-reading) and the corrective (open the live feed first). Don't repeat it.

---

## Priority order

Updated 2026-10-06, after the scoring work below merged. Items 1 and 2 block each other:
nothing can be released until a production run has executed the new code and been read
(`RELEASING.md` §0).

1. **[BLOCKING] Live-verify the 2026-10-06 scoring changes.** Thirteen commits sit on `main`
   unreleased and **none has run in production**. Read `curator_candidates` for two
   consecutive 07:00 UTC runs:
   - **Day one will still look arXiv-heavy and that is expected.** The stored
     `recent_topics` is `['LLMs','Vision/Robot','Policy/Society','Compute/HW','LLMs']` with
     no `General` in it, so the new cooldown needs one run to write one and wash through.
     Do not judge the change on day one.
   - **Day two is the signal.** Baselines to beat, measured over the 21 logged runs from
     2026-09-13 to 10-04: a primary source appeared in the shortlist in **2 of 21** runs;
     **13 of 21** runs offered exactly **one** distinct publisher; 79 of 105 candidate
     slots were arXiv.
   - Also read the new `keyword_substring_only_matches` line: how often the old loose
     keyword matching was paying out across the whole pool, not just in titles.
2. **[BLOCKED on 1] Cut the release.** Thirteen commits since v4.27.1: #159, #160, #161,
   #162, #163, #165, #167, #168, #170, #171, #172, #173, #174. Minor, not patch — a reader
   of the feed can see the difference (which story gets picked). Follow `RELEASING.md`:
   version in four places, BACKLOG header + changelog line, annotated tag, GitHub release
   separating **live-verified** from **test-covered only**, then the wiki.
3. **[BLOCKED on 2] Wiki debt**, now three releases deep and the thing `RELEASING.md` §3
   exists to stop:
   - `Configuration.md`: `TOPIC_REPEAT_PENALTY`, `TOPIC_REPEAT_DECAY`,
     `RECENT_TOPICS_WINDOW`, `TIME_DECAY_PER_HOUR`, `TIME_DECAY_MAX`,
     `TIME_DECAY_MIN_AGE_HOURS`, `TIER1_SOURCE_SCORE`, `MIN_GEM_CANDIDATES`,
     `MAX_GEM_CANDIDATES`, `MIN_TIER1_CANDIDATES`; `MOMENTUM_PRODUCTS` is refreshed
     monthly now, not "quarterly by hand".
   - `Troubleshooting.md`: `curator_candidates`, `em_dash_detected`,
     `keyword_substring_only_matches`, `tier1_source_promoted`.
   - `Architecture.md`: `select_candidates` (the shortlist is source-mixed, not
     top-5-by-score) and `source_tier` / `_host_matches`.
   - `Home.md`: version line and test count (**980**).
4. **De-bias phase 2 — bound the keyword magnitudes.** `PRODUCT` (+5) and `GROUNDBREAKING`
   (+7) can total +12 against a tier spread of 7 (3.0 → 10.0), so text signals outvote
   provenance. **Deliberately gated on a week of item 1's
   `keyword_substring_only_matches` data** — phase 2 is re-tuning magic numbers by hand,
   which is what the abandoned landmark gate (§3) teaches against doing without data.
5. **Re-run the Curator judgement test, ~2026-10-20.** Position entropy cannot tell a wise
   choice from an arbitrary one; tier capture when candidate quality *varies within a
   batch* can (framing from an outside review, 2026-10-06). Currently unanswerable: only
   **5 of 21** batches posed a real test at all, and the model captured the top-tier item
   in 1 of those 5 — n too small to mean anything. Item 1's fix is what makes the metric
   meaningful.
6. **Explicit `--mode`.** `main.py` picks the mode from `current_hour < 11`, so a dispatch
   delayed past 11:00 UTC would silently turn a Curator run into a Mentor run. Never
   observed (44 of 44 runs mapped correctly, 22× 07:00→curator, 22× 14:30→mentor) and it
   needs a four-hour delay, so this is cheap insurance, not a live defect. The workflow
   already has a `force_mode` input; the cron dispatches just do not pass it.
7. **Remaining open issues** — fix when convenient (see §2)
8. **Observational items** — wait for more runs, then decide (see §3)
9. **The plan** — `PLAN_engagement.md` covers everything else (see §4). Phase 2 and 3 stay
   data-gated. Phase 4b is code-complete and **parked by decision**, not merely dormant
   (see §1).

**CLOSED 2026-10-06 — voice: the em-dash rule works, no follow-up needed.** The 2026-09-12
rule said to add reject-and-regenerate only if `em_dash_detected` did not fall to ~0. Across
44 production runs from 2026-09-13 to 10-06: **`em_dash_detected` 0, `hype_words_detected`
0.** Both detectors stay as measurement; neither needs escalating to a hard reject.

Post-length hard enforcement **shipped in v4.15.3** (2026-04-22) — see §2 for the retro.

---

## §1 — Goal change executed: Option 1 (build a following) — **SUPERSEDED 2026-10-06**

> **SUPERSEDED 2026-10-06.** The goal is now: run reliably, cost no recurring human time,
> never embarrass its author. Growth is deprioritised. The record below is history, not
> current intent.
>
> **Why.** Growth here needs replies, and replies need Phase 4b (§2.1), which is
> human-gated by design. Frederik declined to staff that approval queue, so the bot stays a
> pure broadcaster. Measured that day: Bluesky 26 → 78 followers over five months and 392
> posts; Phase 4b has run once ever (2026-05-21), one draft, rejected; 68 posts in 30 days
> drew 42 interactions, 41 of them zero — too little signal to steer by.
>
> **To reopen:** staff the Phase 4b queue and start at §2.1. Nothing is deleted.

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

## §2 — Closed issues

Nothing here is open. Every item this section tracked was resolved, and the detail
lives where it belongs: the commit that fixed it, its PR, and the release notes.

The two lessons that were worth keeping are promoted into `AGENTS.md` review
priorities §3 (capture the error message, not just its type) and §4 (state-persistence
failures log at ERROR, not WARN) — binding rules rather than stories.

Resolved here, newest first: the 2026-09-09 freeze-audit leftovers (#133–#139); phantom
writes to post-dependent state (v4.25.1); the duplicate-source-posts follow-ups
(`2be1148`, `76f1287`); the whole-codebase mypy gate; `ruff` in CI; post-length hard
enforcement (v4.15.3); broken-promise teasers; the `post_metrics.json` formatting-feature
schema; the Bluesky session cache; the model-priority chain; and the Imagen 3 image path.
Full evidence for the freeze audit is in its ledger at
`C:/claude/bluesky-bot/scratch/AUDIT_2026-09-09_ledger.md` (local, not in the repo).

---

## §3 — Observational (wait for data)

- **Cheap-LLM scoring pass — the half of this that is still open.** Most of what this entry described shipped on 2026-10-06: the magnitude-blind −12 topic penalty is now a recency-decayed cooldown below the tier spread, the Curator records the topic it actually posted, time decay is bounded, keyword matching is word-anchored, and the shortlist is source-mixed rather than `ranked[:limit]`. The trigger ("launches still being missed after v4.25.0's feeds have had a few weeks") fired on Gemini 4 Argon, 2026-09-30, and that is what prompted the work.

  **What is left is the instrument question**, and it is parked pending data. A hand-weighted linear sum on an uncalibrated scale is arguably the wrong tool for "which story matters most today": keyword density tracks genre, not importance, so a terse tier-10 launch ("Argon is live.") still loses to a mediocre preprint that happens to say "benchmark". Measured 2026-10-06: 6.50 against 14.75.

  **Do not rebuild an exemption gate.** Two attempts, ~11 review rounds, neither converged: (1) detect "this is a launch" from the headline with a regex — leaked on word order, punctuation, decimal versions, mid-word stems, title/description bleed, short names ("o3" inside "o365"); (2) count distinct publishers covering the same flagship — leaked on headline variance, alias splitting ("GPT-5" vs "GPT 5"), and same-org domains. Both infer importance from text, which is unbounded. A second opinion (Gemini, 2026-09-04) agreed independently, and also shot down moving diversity to a selection-time score margin.

  **The direction, when it is worth doing:** a cheap-LLM pass over the top 15–20 candidates — classify topic, detect genuine launches, assign a 1–10 impact score. Fractions of a cent per run, and it deletes the regex surface rather than patching it. **Validate it in shadow mode first:** log its top 3 beside the heuristic's top 3 for two weeks and compare, wiring nothing to the posting path. Parked 2026-10-06 because the shadow run ends in a decision someone has to read, and that is a recurring human cost (`AGENTS.md`, operating goal).

  **Before any of that, read the data already being collected:** `keyword_substring_only_matches` (how much the old loose matching was paying out) and the de-bias phase 2 question in the priority list.

- **Mastodon discovery tags — three things the probe cannot prove (live 2026-09-09).** Tags ship on every Mastodon post. `Mastodon Tag Probe (diagnostic)` is the standing check; re-run it after any prompt or model change and read BOTH numbers, because retention alone cannot validate a reviewer.

  **1. The voice gate has never fired on real output.** `raw` and `sanitized` have been identical in every probe run — the model has never once proposed a hype word or mood tag — so `#Revolutionary` has only ever been rejected in a unit test. The gate is proven by test, not by traffic. Watch the logs for `mastodon_tags_rejected`: the first one is the gate earning its place.

  **2. Two model passes can still both be wrong.** Cross-model review removes the self-approval path; it does not make semantic errors impossible, and nothing available does. This is accepted residual risk, not an unfinished fix. The blast radius is one wrong tag on one Mastodon post, deletable, and the rollback is `MASTODON_TAGS_ENABLED = False`.

  **3. Tag quality is a brand judgement, and the output is not deterministic.** The same post produced `#OOP` on one run and `#ObjectOrientedProgramming` on another, `#AIObservability` then `#Observability`. Read a week of the live Mastodon feed rather than the probe. If tags read off-topic more than occasionally the fix is the prompt (`agents._MASTODON_TAG_PROMPT`), never a bigger keyword map — that is the same text-inference dead end the landmark gate died on twice.

  **Watch the logs too:** `mastodon_tags_invalid_verdict` means the reviewer stopped returning usable decision sets (a model regression, distinct from a genuine all-DROP), and `mastodon_tags_no_reviewer` means discovery pruned the chain to one model and tagging correctly switched itself off.
- **The Register main feed drift — TRIGGER FIRED 2026-10-06, decide.** `https://www.theregister.com/headlines.atom` was added in v4.13 alongside the software-specific feed, with the condition: remove it if Curator runs start surfacing space/security-humour that is not AI/tech dev-relevant. They do. A live pool on 2026-10-06 carried "Only the finest Swedish Bork will do for Stockholm station", "Scientists find planet made on new matter recipe" and "BepiColombo sheds its ride" from that feed, all scoring into the top 12. Drop `headlines.atom` and keep `/software/headlines.atom`, or decide explicitly that the broad-IT diet is worth the noise.
- **`CONSENSUS_SYNERGY_BONUS` retune.** Currently `1.5` per additional feed. Across 27 feeds, a viral story covered by 5+ sources gets `+6.0` on top of its base score — could start dominating every Curator run. Drop to `1.2` if the Curator starts repeatedly picking the same wire-story everyone covers over genuinely distinctive items.
- **`google-genai` 2.x migration.** Currently pinned at `1.75.0` and shipping fine — `gemini-3.5-flash` is producing sharp content. Latest is `2.2.0`, but 1.x is still receiving releases (`1.75.0` exists alongside `2.2.0`), no Dependabot alerts open against the package, and no 2.x capability is on the immediate roadmap. Migrating now is the exact "infrastructure-over-output" trap the 2026-05-08 retro named. Triggers to revisit: (a) a run fails with a 1.x-specific SDK bug (diagnostic surface will catch it), (b) Dependabot files a CVE, (c) Phase 4b or another planned feature needs a 2.x-only capability. Until one fires, the pin stays.
- **`config.py` split — trigger long since passed.** The `utils.py` half of this item is **done**: #81–#84 split it into `state_store` / `net_safety` / `retry` / `news`, and it is 196 lines now, not the 923 this item was written against. `config.py` went the other way — 525 lines then, **1007 now**. The remaining plan: constants stay, prompt text moves to `prompts.py`, curated data to `src/data/`. Mechanical, ~3h with tests. Not urgent, but stop citing a trigger that already fired.
- **Mentor estimation-topic over-saturation — revisit if it recurs.** A snapshot on 2026-05-21 showed 4 estimation-variant posts in 25, suggesting the seed "estimating your own time vs estimating someone else's" was over-firing. On re-check 2026-05-24, post-rewrite data showed 1 estimation post in 2 Mentor runs — within expected range for a 12-seed pool with 5-slot dedup (`main.py:553`). The earlier saturation likely came from the pre-2026-05-15 pool where broad anchors like "Career" pulled toward estimation under multiple seeds. **Revisit if Mentor shows >25% estimation rate over a 2-week window.** Fix options when triggered: widen dedup window from 5 to 8, or split the estimation seed into two narrower seeds, or drop it temporarily. Don't act prophylactically — let the validators run.
- **Lockfile-check vs Dependabot `--strip-extras` friction — durable fix when it gets annoying.** The `.github/workflows/lockfile-check.yml` workflow runs `pip-compile --strip-extras` and diffs the result against committed `requirements.txt`. Dependabot regenerates *without* `--strip-extras`, so it keeps proposing `google-auth[requests]` (with the extra) while the canonical CI output is `google-auth` (no extra) — every Dependabot PR's lockfile check fails until hand-corrected. This bit us 3 times (#46, #47, #49→#50); #50 (2026-06-09) fixed main itself (it had been red for 3+ weeks on this exact line) but did not fix the recurring friction. **Durable fix when the manual-correction tax gets annoying:** drop `--strip-extras` from the workflow so CI matches Dependabot's output. Needs a careful Linux `pip-compile` (NOT Windows — it injects win32-only `colorama`) to confirm no other extras leak in once stripping is off, then commit the regenerated lockfile. ~20 min on a Linux box or via a throwaway CI run. Until then: each Dependabot deps PR needs the `google-auth[requests]` → `google-auth` hand-edit (or supersede with a manual bump branch like #50). Not urgent — main is green; this is about reducing per-PR toil.
- **Audit script Mastodon path.** `scripts/audit_watchlist.py` returns opaque HTML errors when the Mastodon side fails. **2026-05-05**: added a `account_verify_credentials` preflight that runs once before iterating candidates and surfaces a clear message ("token belongs to @user" on success; "MASTODON_API_BASE_URL is wrong / token under-scoped" on failure with the curl command to verify). Root-cause fix is still in `.env` — the user has `MASTODON_API_BASE_URL=https://mastodon.social/@askfred` (a profile URL) where `https://mastodon.social` (the API base) is needed. Bot's posting still works in Actions because the GitHub secret has the right value; only local audit runs are affected. Revisit when adding new Mastodon-only candidates would benefit from automated scoring.
- **Voice formatting A/B — Track C is moot under the current goal.** Observation 2026-04-27: the feed feels flat, so maybe occasional emojis or a topical hashtag would lift engagement. Track A (instrumentation) and Track B (image reliability) both shipped. **Track C never will in this form:** it was gated on "4+ weeks of engagement data" plus brand approval, and engagement data at this scale is noise (68 posts, 42 interactions, 41 of them zero), so it cannot decide anything. `AGENTS.md` now says not to tune on it.

  What survives is the brand question, which never needed the data: **voice is a brand decision, not a metric decision.** If emojis or question hooks are ever wanted, it is Frederik's call on how the feed should read, made once and applied consistently — not an experiment. A reader returning to a feed that starts using emojis after 137 dry posts notices the inconsistency either way.

  **Not precedent:** v4.26.0 shipped Mastodon discovery tags outside this gate, on a *mechanism* argument (a followed tag is Mastodon's discovery engine, the tags sit at the broadcaster, generation is unchanged). That says nothing about emojis, question hooks, or hashtags on the Bluesky copy.

These are judgement calls, not data questions. Engagement at this scale cannot settle any of them — decide by reading the feed.

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
- 2026-09-04: **v4.25.1 — phantom writes to post-dependent state.** `persistence_stage` wrote three cooldowns unconditionally (`recent_topics`, `recent_mode_topics`, `pioneer_recent`) because `main()` calls it regardless of outcome and `AutomationPayload` dropped the `sent_uris` `BroadcastPayload` already carried. A run that delivered nothing therefore suppressed content that never ran — a false −12 topic penalty, a skipped Mentor topic, and a Pioneer entry burning its multi-week cooldown unposted (from a block commented *"only when a pioneer post actually fired"*). Separately the Curator recorded `news_items[0]`'s category even when the model wrote about a different item via the `chosen_link` contract (#51) — the link card was realigned, the topic memory was not. Both fixed in #110: `delivered = bool(bsky_sent_uris or mastodon_sent_ids)` gates every post-dependent write, and the posted category is resolved once in `broadcasting_stage` alongside the link-card realignment so the two consumers of the choice cannot drift again. No migration needed (both stores self-heal). **Process note:** the approach was reviewed by Codex *before* implementation (#110 began as a plan-only draft) — it passed review first time, against eleven rounds for the abandoned landmark gate built the other way round.
- 2026-09-09: **v4.26.0 — Mastodon-only discovery tags.** Hashtags help posts spread on Mastodon and do nothing on Bluesky, because only one of those platforms uses followed tags as a discovery mechanism. So 1–2 tags are appended to the Mastodon root post only, at broadcast time, and the Bluesky copy is untouched. Root post only: it is what gets boosted, and tagging every post in a thread spams the tag timeline with one item.

  **Rejected first: a fixed per-category tag map.** It was built, then thrown away — it would stamp the same two tags on every Mentor post forever (a bot tell in the profile view) and put posts in tag timelines they do not belong in. The repo already held that position: `_extract_style_fingerprints` tracks `repeated_hashtags` and tells the model to vary its selection. Tags are instead named per-post by a model that reads the finished text.

  **Four gates stand between a suggestion and the feed.** A lexical gate rejecting `BANNED_HYPE_WORDS` / `BANNED_TEASER_PATTERNS` (normalised to bare alphanumerics and matched by containment, so `#GameChanging` and `#RevolutionaryAI` are caught) plus the mood tags in `MASTODON_TAGS_EXCLUDED`; a semantic review by a **different** model (`gemini-3.7-flash` proposes, `gemini-3.5-flash` reviews, and **no distinct reviewer means no tags**); a strict verdict parser requiring a complete decision set (real ints, in range, no duplicates, full coverage) or the whole response is discarded; and the shared `MAX_HASHTAGS_PER_POST` ceiling applied to the post *total* rather than per source, so a Mastodon post can never carry more hashtags than the same post on Bluesky is allowed.

  **Cannot fail or delay a run.** Tagging happens inside the Mastodon coroutine, so both broadcasts start together and only Mastodon pays for its own tags; one attempt per call inside a shared 20s budget; every failure path — model error, unparseable verdict, nothing surviving either gate, the deadline expiring, no Mastodon token — ships the post untagged. `BroadcastResult.delivered_texts` carries what actually went on the wire, so a metrics row cannot describe a post that was never sent.

  **The process is the story.** The feature was enabled, reverted fifteen minutes later, and re-enabled two hours after that. Both holes that forced the revert were found by Codex review that landed ~90 seconds *after* each merge and was not read in time. What changed the outcome was not more care in the abstract but a specific discipline: measure **rejection**, not agreement. Ten posts the model tagged sensibly said nothing about what it does with a bad tag. See §3 for what the probe still cannot prove.

- 2026-09-09: **Freeze audit, #121–#131.** A whole-system audit against the freeze tag `audit-freeze-2026-09-09` (v4.26.0). Two reviewers, Claude and then Codex, got the same fixed scope, and every finding was cross-examined with a trace before any code was written. Ten fixes merged: the Curator no longer marks every candidate as seen (#121); a failed state read no longer erases the seen state (#124) or `replied_to` (#130), and skips the mention pass so nobody gets answered twice; a 3xx is no longer reported as a stored write (#122); a bad og:image no longer discards the headline (#123); an idempotency key stops a Mastodon timeout retry from double-posting (#125); each mention reply is recorded as it lands (#126); state is persisted before the best-effort stages (#128); settings knobs nothing read were removed (#127); and four small rows were batched, including the mypy config moving into `pyproject.toml` (#129). Six confirmed findings were left for later and filed in §2 (#131). Five were refuted under cross-examination and are recorded in the audit ledger so they are not re-filed.
- 2026-09-10: **Audit leftovers, #132–#140.** Mastodon thread parts are numbered "1/2", "2/2", because Mastodon lists a self-thread newest-first (#132). Curator posts carry their source link in the Mastodon text (#133): the audit had rated this medium, and the live feed showed 4 of the last 5 Curator posts reaching Mastodon with no source. The proactive scan and the approval share one concurrency group, closing a write race before Phase 4b is activated (#134). Cleanups: an unused `handle_interactions` parameter (#135), `scripts/` in the mypy gate (#136), one image compressor for every path (#137), the publisher thumbnail validated so the Curator's fallback image can run (#138), the `src.utils` re-export shim retired (#139), and three dead items removed, including the `retry_with_backoff` decorator that #85 had kept (#140). Verified in production the same day, from the state Gist's revision history: a Curator run now adds one seen link instead of five (#121), and state is written six seconds after the post, before the metrics stages (#128). #132 and #133 wait on the next Curator run for their live check.
- 2026-09-11: **v4.27.0 — Mastodon parity and the freeze-audit fixes.** Release of #121–#148, the first cut with `docs/RELEASING.md` (#149). Since the two entries above came the repo-quality pass (#142–#147): required CI on `main`, `check_untyped_defs`, mention replies fitted to a whole sentence, feeds 33 → 28 with Hacker News on its own feed, and a mutation run whose gaps in the posting, retry and state code are now pinned. Then #148 stopped a stray local `.bak` from making an empty state look trusted. Live-verified on the 2026-09-11 09:00 CEST run: the Mastodon source link and thread numbering, the official HN feed, and a clean run on the real entry point. Test-covered only: the reply fitting (no mentions yet), the `.bak` path (never reached on Actions), the Mastodon idempotency key, and the Phase 4b concurrency group.
- 2026-09-12: **v4.27.1 — Feed-health issues and a second mutation run.** Release of #151–#157. A dead feed opens a `feed-health` GitHub issue after the run that notices it, and closes it once every feed is healthy (#153): its first two runs logged `0 flagged, action=nothing`. `pip-audit` replaces the Snyk check (#151). A second mutation run covered `net_safety.py`, `news.py` and `agents.py`: 213 survivors, 156 killed by new tests, the rest equivalent and listed in each test file (#155, #156). Small fixes: whole-link source-link match and the dead `hnrss.org` tier (#154), a 75-second test sleep (#152), the Mastodon bio copy (#157). 728 → 861 tests, coverage 92% → 95%. Nothing in the feed changed.
