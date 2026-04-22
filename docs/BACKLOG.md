# Backlog

Living list of pending work and parked ideas. Bot is shipping fine at v4.15.2. Nothing here is urgent — the ordering below is what I'd tackle in sequence if I had the time.

---

## Priority order

1. **Engagement feedback — Slice A** (collect data; see §1)
2. **Open issues from the Apr 22 run** — fix when convenient (see §2)
3. **Observational items** — wait for more runs, then decide (see §3)
4. **Bigger parked plans** — execute when there's a lull or a trigger (see §4)
5. **Future ideas** — not yet plans; note them so they don't get lost (see §5)

The reason §1 is first: every item in §2–§5 currently decides on gut feel. Slice A of the engagement plan is ~3h of work that turns the whole rest of this list from "guess and hope" into "check the data, then decide." Nothing else unlocks as much.

---

## §1 — Next up

### Engagement feedback — Slice A (data collection only)

See `docs/PLAN_engagement_feedback.md`. Slice A captures post IDs at broadcast time, fetches likes/reposts/replies for posts 24h–30d old on each run, and writes a new `post_metrics.json` to the Gist. Zero behaviour change — no scoring adjustments, no voice changes.

Effort: ~3h. Output: real data flowing. After two runs you can verify it works; after two weeks you have enough to inform every other decision on this list.

**Slice B** (weekly digest workflow) follows naturally once A is running — another ~1h.
**Slice C** (feeding signals back into scoring) only after 4+ weeks of data — ~3h.

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

Both decisions get trivial once §1 (engagement feedback) is running — no more guessing at "is the Register feed hurting?"; look at the engagement data.

---

## §4 — Parked plans (ready to execute on trigger)

Everything here has a written plan. When the trigger fires, the work is 15 min of re-reading, not a fresh design session.

- **`docs/PLAN_engagement_feedback.md` — feedback loop.** Slice A is §1 above. Slice B (weekly digest) and Slice C (scoring ingestion) follow. **Strategist-fallback frequency** falls out of Slice B's digest for free — no separate plan needed.
- **`docs/PLAN_proactive_replies.md` — proactive replies.** Three phases: virtual-follow recon script (~1.5h) → 2–3 handles behind human-approval gate (~4h) → expansion once traction shows. Approval gate is the guardrail against tone-deaf-reply blast radius.
- **`docs/PLAN_feed_health.md` — feed health observability.** ~1.5h. Capture `FeedFetchResult` per feed → `feed_health.json` in Gist → weekly digest flags silently-dead feeds. Trigger: after engagement Slice A ships (shares infrastructure).
- **`docs/PLAN_per_post_idempotency.md` — thread-broadcast idempotency.** ~2h. Fix `retry_with_backoff` re-sending posts 1–2 when post 3 of a thread 429s. Trigger: a duplicate actually appears in production. Until then: premature — threads are mostly 1–2 posts now.
- **`docs/PLAN_v4.16_slim.md` — three-slice refactor.** Split `src/utils.py` (923 lines), split `src/config.py` (525 lines), replace 3-stage frozen dataclass chain with `RunContext`. Pure hygiene. Execute when the next feature would benefit from the cleaner shape, not before.

---

## §5 — Future ideas (not yet plans)

Empty for now. When a new idea shows up that's not yet concrete enough for a plan, park it here. Consolidation on 2026-04-22 promoted everything that was previously in §5 into real plans in §4.

---

## Rejected

- **Threads (Meta) as a broadcast target.** Rejected multiple times. Don't resurrect.

---

## Changelog

- 2026-04-22: Pioneer-dimension telemetry item removed from §5 — subsumed by `PLAN_engagement_feedback` Slice A/B (post metrics already capture `pioneer_id` context, so pioneer-category performance falls out of the digest for free).
- 2026-04-22: Consolidated §5 future ideas into §4 plans. Promoted per-post idempotency and feed health into their own plan docs (`PLAN_per_post_idempotency.md`, `PLAN_feed_health.md`). Strategist-fallback frequency folded into `PLAN_engagement_feedback.md` Slice B (free rider of the digest, since `mode` is already in the post_metrics schema). §5 is now empty — when data-driven triggers fire, the plans are ready to execute.
