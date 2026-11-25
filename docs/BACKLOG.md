# Backlog

The goal and the open queue, on one screen. The direction is
[`PLAN_2027.md`](PLAN_2027.md) (approved 2026-11-16): read it first. Everything this file
used to hold — closed items, findings, observations, the changelog — is in
[`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md), as history to mine, not as limits.

## §1 — Operating goal (since 2026-11-16)

> **Purposeful growth and professionalism.** Reach the people this perspective is for,
> without giving in on reliability, trust or professionalism. Not growth for the sake of
> growth.
>
> **Why.** Frederik, 2026-11-16: reactions to the posts are growing, and he reads them and
> answers the good ones himself. Reaching out to people is wanted; giving in on reliability,
> trust or professionalism to do it is not. The same day the automatic mention replies were
> switched off (#273): the account's replies are his own. This text is canonical;
> `AGENTS.md` and `.gemini/styleguide.md` carry it verbatim.

## §2 — Objectives

The four 2027 objectives, how each is measured, and the six guardrails are in
[`PLAN_2027.md`](PLAN_2027.md). They are not copied here, so there is one version of them.

## §3 — Open queue

Anything meant to be built is a GitHub issue, not a bullet here.

- [#293](https://github.com/askfredbv/bluesky-bot/issues/293) — Secrets now environment-only: live-verify every workflow that uses them
- [#295](https://github.com/askfredbv/bluesky-bot/issues/295) — v4.33.0 and the loose ends from 2026-11-16
- [#264](https://github.com/askfredbv/bluesky-bot/issues/264) — De-bias phase 2: bound the keyword magnitudes
- [#269](https://github.com/askfredbv/bluesky-bot/issues/269) — Architecture direction: answer the five owner questions
- [#255](https://github.com/askfredbv/bluesky-bot/issues/255) — Recheck Gemini Code Assist sunset impact before 2027-01-31
- [#332](https://github.com/askfredbv/bluesky-bot/issues/332) — Voice drift: anecdote posts read as summary; contraction in a Register A post

## §4 — Later (no issue until it is built)

- **Explicit mode on the scheduled dispatches** (was #265; PLAN_2027 item, by 2027-03-31).
  `main.py` picks the mode from the UTC hour, so a dispatch delayed three hours or more
  would turn a morning run into an afternoon one. Never observed (44 of 44 runs mapped
  correctly). Cheap insurance, not a live defect.
  - **Do not pass `force_mode` on the cron dispatches.** The afternoon alternates Mentor
    and Strategist by day parity (`mode_selection_stage`); a fixed `force_mode=mentor`
    would make Strategist unreachable, and `force_mode` is a test hook that logs
    `mode_forced` on every use.
  - **Instead, a `slot` input (`morning` / `afternoon`).** `mode_selection_stage` uses it
    when given and falls back to the UTC hour when blank; `afternoon` keeps the parity
    alternation. About 5 lines in `main.py`, the input in `daily_post.yml`, 2-3 tests.
  - **Rollout order matters.** GitHub answers 422 to a `workflow_dispatch` that sends an
    input the workflow does not define, so a wrong order means no post that run. Merge
    the repo change first, then add `"inputs":{"slot":"morning"}` / `"afternoon"` to the
    two cron-job.org job bodies. If the input is ever removed, change the cron bodies first.
  - Also hour-based, decide when building: the session label (`src/agents.py`,
    `now.hour < 12`) and the morning/afternoon split in `src/metrics.py`.
