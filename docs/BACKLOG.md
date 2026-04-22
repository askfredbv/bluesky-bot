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

- **`docs/PLAN_engagement_feedback.md` — feedback loop.** Slice A is §1 above. Slice B (weekly digest) and Slice C (scoring ingestion) follow.
- **`docs/PLAN_proactive_replies.md` — proactive replies.** Three phases: virtual-follow recon script (~1.5h) → 2–3 handles behind human-approval gate (~4h) → expansion once traction shows. Approval gate is the guardrail against tone-deaf-reply blast radius.
- **`docs/PLAN_v4.16_slim.md` — three-slice refactor.** Split `src/utils.py` (923 lines), split `src/config.py` (525 lines), replace 3-stage frozen dataclass chain with `RunContext`. Pure hygiene. Execute when the next feature would benefit from the cleaner shape, not before.

---

## §5 — Future ideas (not yet plans)

- **Per-post idempotency for thread broadcasts.** `retry_with_backoff` re-runs the whole `post_to_bluesky` call on failure, so a 429 mid-thread can duplicate posts 1–2 on retry. Fix requires tracking which posts in a thread were already sent (in-memory `thread_index → at://uri`). Only worth it if duplicates actually appear in production.
- **Strategist-fallback frequency check.** Curator silently shifts to Strategist when news volume is <3 items. One-shot log grep: if firing <1/week the complexity is earning its keep; if multiple times/week the news pipeline needs tuning. ~10 min of work.
- **Feed health dashboard.** Lightweight "feed last yielded an accepted item N days ago" signal in the Actions summary to catch silently-dead RSS feeds. Fits naturally alongside engagement feedback (same data-collection pattern).

---

## Rejected

- **Threads (Meta) as a broadcast target.** Rejected multiple times. Don't resurrect.

---

## Changelog

- 2026-04-22: Pioneer-dimension telemetry item removed from §5 — subsumed by `PLAN_engagement_feedback` Slice A/B (post metrics already capture `pioneer_id` context, so pioneer-category performance falls out of the digest for free).
