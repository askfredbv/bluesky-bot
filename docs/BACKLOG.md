# Backlog

Living list of pending work and parked ideas. Bot is shipping fine at v4.15.2. Nothing here is urgent — the ordering below is what I'd tackle in sequence if I had the time.

---

## Priority order

1. **`PLAN_engagement.md` Phase 1** (capture metrics + feed health + per-post idempotency; see §1)
2. **Open issues from the Apr 22 run** — fix when convenient (see §2)
3. **Observational items** — wait for more runs, then decide (see §3)
4. **The plan** — `PLAN_engagement.md` covers everything else (see §4)

The reason §1 is first: every item in §2–§4 currently decides on gut feel. Phase 1 of the engagement plan is ~5h that turns the whole rest of this list from "guess and hope" into "check the data, then decide." Nothing else unlocks as much.

---

## §1 — Next up

### `PLAN_engagement.md` Phase 1 — Capture (data + observability + idempotency)

See `docs/PLAN_engagement.md`. Phase 1 bundles three changes that share a broadcaster signature change (`BroadcastResult`):

- **Post metrics** — capture post IDs at broadcast, refresh likes/reposts/replies for posts 24h–30d old, write `post_metrics.json` to the Gist
- **Feed health** — `fetch_single_feed` returns `FeedFetchResult`; aggregated into `feed_health.json` in the Gist
- **Per-post idempotency** — drop `@retry_with_backoff` from broadcasters, add per-post retry, emit `*_partial_delivery` on exhaustion. Fixes the silent re-send bug on Mastodon

Effort: ~5h. Output: real data flowing + Mastodon's silent re-send pattern fixed. After two runs you can verify it works; after two weeks you have enough to run Phase 2 (digest) usefully.

---

## §2 — Open issues (found in the April 22 run)

### Snapshot Gist state step 404s — cosmetic, bot unaffected

Added in `7fe67ce` (April 21). Post-run step calls `GET /gists/{id}` via `urllib` and 404s, while the bot itself read and wrote the same Gist successfully in the same run.

Fix order:
1. **Add a `User-Agent` header** to the urllib request — GitHub sometimes 404s on missing UA. One-line change.
2. **Swap urllib for `httpx`** to match the bot's code path. Removes the comparison gap.
3. **Delete the step entirely.** The Gist is already the persistent store and has built-in version history via `GET /gists/{id}/{sha}` — the Actions artifact is belt-and-suspenders that's now noisy.

Try #1; default to #3 if that fails.

### Curator post 0 generated 476 chars vs 300-char cap — quality regression

Defensive splitter caught it at a word boundary, so the post landed. But a 58% overshoot means the v4.14 single-post-default is being silently undone by the splitter path.

**Measure before fixing.** Grep the last 2 weeks of Actions logs for `post_length_exceeded`. If it's happening ~once a week, not worth a fix. If it's most runs, prompt has drifted.

Candidate fixes:
1. Tighten `SYSTEM_INSTRUCTIONS_CURATOR` — explicit "your post body must be under 260 chars; URL/handle room comes out of that budget."
2. Regenerate on overshoot — if post 0 > 300 chars, retry once with a "your last draft was N chars over the limit — rewrite shorter" follow-up. Keeps the single-post default when the model gets it wrong.
3. Lower the prompt's target to 250 chars so natural overshoot lands under 300 (hackier).

---

## §3 — Observational (wait for data)

- **The Register main feed drift.** `https://www.theregister.com/headlines.atom` added in v4.13 alongside the software-specific feed. If Curator runs start surfacing space/security-humour content that isn't AI/tech dev-relevant, remove it from `RSS_FEEDS`. The `/software/headlines.atom` feed stays either way.
- **`CONSENSUS_SYNERGY_BONUS` retune.** Currently `1.5` per additional feed. With 25 feeds, a viral story covered by 5+ sources gets `+6.0` on top of its base score — could start dominating every Curator run. Drop to `1.2` if the Curator starts repeatedly picking the same wire-story everyone covers over genuinely distinctive items.
- **v4.16 slim refactor.** When `src/utils.py` (923 lines) or `src/config.py` (525 lines) grows another ~100 lines, do the split-into-focused-modules refactor before the next feature. Target layout: `state_io.py`, `feeds.py`, `scoring.py`, `url_safety.py`, `retry.py`, `image_io.py`; `config.py` keeps tunable constants only, prompt text moves to `prompts.py`, curated data to `src/data/{pioneer,feeds,topics}.py`. Mechanical; ~3h with tests. No plan doc needed — when the trigger fires, the layout above is the plan.

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
