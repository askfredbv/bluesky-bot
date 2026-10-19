# Review: what to take from BluBot (2026-10-19)

A read of [strike007-3000/BluBot](https://github.com/strike007-3000/BluBot) — Shreyas's
bot, same problem, independent codebase — covering the twelve PRs he merged on
2026-10-17/18 (#184 → #195, v3.27.0 → v3.35.1). Read-only: nothing here is built and
nothing is queued.

Purpose is to separate **could** from **should**, because the two lists are very
different lengths.

## Method and what it is worth

Read the repo, the twelve PR bodies, and the code behind each claim; then checked every
idea against our own source rather than against memory. Several things I expected to be
gaps were not — we already have `Image.MAX_IMAGE_PIXELS`, entropy-based secret
redaction, DNS-pinned SSRF defence, an advisory file lock, model discovery, dependabot
and CodeQL. The two codebases are within 2% of each other on line count: 8,881 ours,
9,039 his.

**Nothing below is validated by audience.** His `STATUS.md` on 2026-10-18 read
*4 posts · 1 likes · 0 reposts · 0 replies* over seven days. His reach is as flat as
ours, so every item here is judged on reliability and craft, which is the lens
`AGENTS.md` asks for anyway.

## The framing that decides the rankings

**None of the twelve PRs touches our binding constraint.** The 2026-10-12 cold read
stands: 2 of 20 live posts cleared the "would I post this" bar, Mentor 0 of 10, and the
variable is whether the post *takes a position*. His batch is plumbing, provenance and
presentation — all competent, none of it moves that number. A bot with a durable ledger,
an RSS archive and a trust dashboard still publishes the same 10%.

So the honest shape of this review: **a long "could" list, a two-item "should" list, and
both sit below the open priority-order items.** Per the working gate, open concrete work
gets finished before new threads are opened.

---

## SHOULD — worth doing on our own terms

### S1. Two-phase pre-broadcast reservation (correctness)

He reserves the story in remote state *before* broadcasting and aborts publication if
that write fails (`reserve_pending_stage`, his `bot.py:622`); after broadcast it settles
to `published`, or to `stage: "uncertain"` if every platform failed.

We do the reverse. `persistence_stage` writes the dedup entry only after delivery
(`main.py:899`), and `main.py:820` deliberately leaves state untouched when nothing
reached a platform. **A run that posts and then dies before the state write can repost
the same story on the next run.** The `links` list is capped at 200 canonical URLs and
the feed window only admits entries from the last two days, so the window is narrow —
and this has not been observed happening. It is a latent hole, not a fire. Worth closing
cheaply; not worth jumping the queue for.

### S2. Feed backoff with automatic recovery (reliability, zero human cost)

His `src/feed_vanguard.py` is 108 lines: one failure warns but keeps the feed active,
two = 1h silence, three = 12h, four+ = 24/48/72h, six = TERMINAL, and any success deletes
the entry and logs a recovery.

We *notice* dead feeds — `check_feed_health_alerts` (`src/metrics.py:243`) splits
**broken** from **stale** on separate thresholds, which is a better diagnosis than his —
but we keep fetching them every run forever. Adding the skip-and-recover half on top of
the health data we already collect is small, self-healing, and costs no human time. The
most direct fit with the operating goal in the whole review.

---

## COULD — real, with a trigger rather than a date

### C1. Durable published-post ledger

His `published_posts` (#184): canonical URL, title, topic, source, supporting sources,
and per-platform root IDs (Bluesky `uri`/`cid`, Mastodon/Threads `id`), deep-merged,
capped at 500, **never time-pruned**.

Ours is `post_metrics` (`src/metrics.py:452`), richer per row — emoji counts, hashtag
counts, question counts, time-of-day buckets — but pruned at 30 days
(`POST_METRICS_MAX_AGE_DAYS`, `src/config.py:221`). So we keep no permanent record of
what the bot has ever published. Cheap on its own; it is also the substrate C2 and C3
both need. **Trigger: do this first if either C2 or C3 is ever wanted.**

### C2. Public archive + RSS, and the trust dashboard

His `scripts/build_archive.py` (1,270 lines, zero runtime dependencies, system fonts,
WCAG AA, works with JS disabled) plus `pages.yml` fired by `workflow_run` on the post
workflow.

The HTML is not the interesting part. The **Source Trust Dashboard** is: corroboration
rate (≥2 independent publisher domains, roundups excluded from the denominator), unique
sources, cross-platform coverage, top-5 source distribution, with the calculation
disclosed in a `<details>`. That is self-measurement that is **not** engagement — it
measures whether the curation is doing its job, which is a question we have and cannot
currently answer. It would also give the bot a citable public surface.

### C3. Weekly deterministic roundup

Friday's slot is replaced by a digest built from the ledger with **zero AI** — plain
`This week in AI (iso-week)` then numbered title/URL pairs — ranked by live engagement,
falling back to recency, skipping entirely if already published or if nothing is
eligible.

No generation risk and no voice risk by construction, which is the appeal: it is the one
content idea in his batch that cannot embarrass its author. Needs C1.

One transferable detail regardless of the rest: `validate_roundup_urls_preserved`
(his `bot.py:895`) runs the real split and hashtag-cleaning pipeline for every platform
and **refuses to broadcast** if a story URL did not survive. The same assertion applied
to our Curator source link would turn a trust-the-trim into a checked invariant.

### C4. Cloudflare Workers AI for generated images

`@cf/black-forest-labs/flux-1-schnell` instead of `gemini-3.1-flash-image`.

Verified pricing, 2026-10-19:

| | ours | his |
|---|---|---|
| list price | $60 / 1M output tokens | 4.80 neurons per 512×512 tile + 9.60 per step |
| per 1024×1024 image | 1,120 tokens ≈ **$0.067** | ~57.6 neurons ≈ **$0.00063** |
| free allocation | **none** for this model | **10,000 neurons/day** (≈170 images) |

Our volume is 1–1.5 images/day — `IMAGE_GENERATION_PROBABILITY = 0.85`
(`src/config.py:468`) gates both the afternoon Mentor/Strategist image and the morning
Curator fallback card (`main.py:365`, which only fires when the article has no OG
thumbnail). So ~$2–3/month at list against $0, with ~99% of the free allocation unused.

**The cost argument is weaker than it looks, by this project's own standard.** The
priority-order note on the Sonnet trial already says cost is not the deciding factor at
"~$3/month against ~$0.16". The same reasoning applies here: $2–3/month does not decide
anything.

**The real argument is dependency.** `PLAN_provider_agnostic.md` names images as the
single point of failure in any Google-free configuration, and every Google image
deprecation so far — imagen-3, imagen-4, the ~2026-08-26 `gemini-3.1-flash-image`
wobble — cost an afternoon of manual model-swapping. Workers AI is the missing fourth
row in that plan's facts table and the only one that removes Google from the image path.

**The open question is aesthetic, not technical.** Schnell is a 4-step distilled model,
and the look of every image in the feed is a brand surface. Settle it by looking, not by
reasoning — hence the probe mode added alongside this review
(`scripts/probe_image_models.py --cloudflare`).

### C5. Diagnostics consolidation

He has one `scripts/diagnostic.py --mode X`, and his `AGENTS.md` rule 11 forbids adding
a new verification script before checking the existing one. We have five:
`probe_fallback`, `probe_image_models`, `probe_mastodon_tags`, `report_feed_health`,
`discover_models`, each with its own workflow. His is tidier. Low value, non-zero churn —
park it until something else makes us touch those files.

---

## NOTED — read, not wanted now

- **Sources beyond RSS.** GitHub fast-rising repos (search API, capped velocity bonus,
  403/429 handling) and Hugging Face trending, both **excluded from RSS health
  tracking** — the detail that keeps it clean. Our feed pool is what priority items 4
  and 5 are already re-tuning; adding source types mid-tune would confound them.
- **Status-aware model rotation.** He rotates on 503/429/400/404 specifically, and
  discovery keeps only exact matches from an approved priority list. We have
  `filter_available_models` (`src/agents.py`) and `model-discovery.yml`; the
  per-status-code runtime rotation is the delta, and it is the shape of fix that would
  have caught the Imagen deprecations earlier.
- **His AGENTS.md rules 1–3** — prefer deleting code, check stdlib and existing
  utilities before writing custom, no new abstractions without real duplication. A
  useful counterweight: we are at 8,881 lines and growing faster than he is.
- **Bot-authored PRs and CI.** His #192 fixes the GITHUB_TOKEN recursion guard with a
  `REPO_TOKEN` PAT so the PR gets real check runs. We solved it in
  `refresh_momentum.yml` by mirroring ruff+pytest in-job. His gets visible green checks;
  ours avoids a write-scoped PAT in a workflow that pushes. **Keep ours.**

## REJECTED

- **Bluesky source receipts** (#185) — auto-replying to corroborated posts with source
  links. Against `AGENTS.md` §6 (outward actions stay human-gated) and reach-shaped.
- **Telegram approval gateway** (his `src/telegram_gateway.py`, 796 lines: inline
  per-platform tabs, conversational draft editing by reply, post-broadcast delivery
  reports). The nicest engineering in his repo, and a recurring-human-time machine —
  which is the thing deprioritised on 2026-10-06. **Caveat worth banking:** if Phase 4b
  is ever revived, *this* is the right approval UX. `workflow_dispatch` is why our
  proactive replies never once posted.
- **Three posts/day on weekdays.** More surface, more risk, and his own numbers show
  volume buying nothing.
- **Dynamic bio with a day-streak counter** ("Day 68") — streak theatre.

---

## PR-by-PR verdict

| PR | What | Verdict |
|---|---|---|
| #184 | Durable published-post ledger | C1 |
| #185 | Source receipts + HF trending | Rejected / Noted |
| #186 | Cloudflare Workers AI image provider | C4 |
| #187 | Cloudflare + Telegram preview hardening | n/a |
| #188 | Report-only engagement → STATUS.md | Have equivalent (`src/metrics.py`) |
| #189 | GitHub fast-rising repos source | Noted |
| #190 | Pages archive + RSS from ledger | C2 |
| #191 | Friday roundup digest | C3 |
| #192 | CI on weekly config PRs | Noted — keep ours |
| #193–#195 | Archive redesign, trust dashboard, a11y | C2 |
| (standing) | Feed backoff with recovery | **S2** |
| (standing) | Two-phase reservation | **S1** |
