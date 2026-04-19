# Backlog

Living list of pending work and parked ideas. Nothing here is urgent — the bot is shipping fine at v4.15.0.

---

## Pending (observational — revisit after more runs)

- **Watch The Register main feed for off-topic drift.** `https://www.theregister.com/headlines.atom` was added in v4.13 alongside the software-specific feed. If Curator runs start surfacing space/security-humour content that isn't AI/tech dev-relevant, remove it from `RSS_FEEDS` in `src/config.py`. The software feed (`/software/headlines.atom`) stays either way.
- **Re-tune `CONSENSUS_SYNERGY_BONUS`.** Currently `1.5` per additional feed. With 25 feeds now (up from 17), a viral story covered by 5+ sources gets `+6.0` on top of its base score, which could start dominating every Curator run. If the Curator starts repeatedly picking the same wire-story everyone covers over genuinely distinctive items, drop to `1.2`. Check after ~2 weeks of runs.

---

## Parked (ready to execute when the time is right)

- **`docs/PLAN_engagement_feedback.md` — feedback loop.** Three-slice plan to get engagement metrics flowing back into scoring. Slice A (data collection, 3h) is the real unlock; Slice B (weekly digest, 1h) follows; Slice C (scoring ingestion, 3h) only after 4+ weeks of Slice A data. Biggest single lever for making every other tuning decision evidence-based instead of gut feel.
- **`docs/PLAN_proactive_replies.md` — proactive replies.** Three-phase plan to move the bot from broadcaster to participant. Phase 1: virtual-follow recon script, ranks ~15 candidate handles by topic fit and voice compatibility (~1.5h). Phase 2: minimum-viable reply loop with 2–3 handles and human approval gate (~4h). Phase 3: expand watchlist once Phase 2 shows traction. Approval gate is the guardrail against tone-deaf-reply blast radius.
- **`docs/PLAN_v4.16_slim.md` — three-slice refactor.**
  - Slice 1: split `src/utils.py` (923 lines) into focused modules (`state_io`, `feeds`, `scoring`, `url_safety`, `retry`, `image_io`)
  - Slice 2: split `src/config.py` (525 lines) — separate knobs from prompt text from data
  - Slice 3: replace 3-stage frozen dataclass chain in `main.py` with a single mutable `RunContext`
  - Not blocking anything today. Good fit for the next lull between features, or the moment before a change that would benefit from the cleaner shape (third persona mode, state-backend swap, etc.).

---

## Future ideas worth keeping

- **Per-post idempotency for thread broadcasts.** `retry_with_backoff` currently re-runs the whole `post_to_bluesky` call on failure, so a 429 mid-thread can duplicate posts 1–2 on retry. Fix requires tracking which posts in a thread have already been sent (e.g. a per-run in-memory map of `thread_index → at://uri`). Flagged as a known limitation in the 429 plan; only worth the effort if duplicates actually appear in production.
- **Pioneer dimension telemetry.** After a few weeks of pioneer posts, look at which categories (pioneer / artifact / project / hero) land best. If one category consistently underperforms, trim it from `PIONEER_FACTS_UNDATED`. Requires either manual review of engagement or a lightweight analytics pass.
- **Anchoring the Strategist fallback.** Currently when news volume is low (<3 items), Curator silently shifts to Strategist on a secondary topic. Worth checking how often this fires in practice — if it's rare (<1/week) the complexity is earning its keep; if it's firing multiple times a week the news pipeline itself needs tuning.
- **Feed health dashboard.** 25 RSS feeds is enough that individual feeds silently going stale is plausible. A lightweight "feed last yielded an accepted item N days ago" signal in the Actions summary would catch dead feeds without needing a separate monitor.

---

## Explicitly rejected (do not resurrect)

- **Threads (Meta) as a broadcast target.** Rejected multiple times. Don't add to roadmap.
