# AGENTS.md — working in (and reviewing) this repo

Guidance for any AI agent authoring or reviewing changes here (Codex, Claude, etc.).
Read this before reviewing a PR: flag violations of the project-specific principles
below, not just generic bugs. Ground every flag in the actual code — verify the claim
holds before asserting it (a reviewer can be wrong too).

## What this project is

A bot that posts AI/tech-mentorship content to **Bluesky** (@askfred.be) and **Mastodon**,
twice daily via GitHub Actions (triggered by cron-job.org). Python 3.11, Gemini for
generation, a private GitHub Gist for state. **Explicit goal: build a following** (real
audience). The voice is the product. Read `docs/RETRO_2026-05-08.md` and `docs/BACKLOG.md`
for the hard-won context before proposing structural changes.

## Review priorities — the things a generic reviewer misses

1. **Voice is a brand decision, not a metric decision.** Content/prompt changes must
   preserve the voice rules: no hype words, no reader-bait questions, no broken-promise
   teasers ("more soon", "stay tuned", 🧵), no source-summary openers ("A new paper…",
   "Researchers found…", "Sobering read."), first-person presence by default, and the two
   registers (strategic-advisory vs casual-narrative) defined in `STYLE_GUIDELINES`
   (`src/config.py`). Flag any change that loosens these, even if it "reads fine".

2. **No hardcoded fallback content that bypasses voice/validation.** The catastrophic
   v4.18 bug shipped a hardcoded `"Notes on {topic} — more soon."` placeholder to
   production for weeks. Rule: **missing a run beats shipping garbage.** On generation
   failure the right behaviour is to return empty and skip the broadcast — never emit
   content that bypasses `_apply_voice_trim` / `validate_summary`. Flag any new fallback
   that produces postable text without passing the validators.

3. **Diagnostic discipline: capture the message, not just the type.** Every `except` that
   logs must include `error_msg=str(e)[:200]` (or equivalent), not `error_type` alone.
   Several multi-day debugging delays (Step 2 KeyError, Imagen 3 deprecation, the
   bluesky_session revocation loop, the gemini-2.5-pro AttributeError) were caused by
   logging the type without the message. Flag any new catch that logs the type alone.

4. **Fail loud on critical state, fail safe on the rest.** State-persistence failures
   (Gist writes) must log at ERROR, not WARN — silent degradation is worse than noise.
   But best-effort work (proactive scan, metrics refresh, a single feed fetch) should
   catch, log, and continue rather than crash the run. Flag the wrong choice in either
   direction.

5. **Phase 4b kill-switch separation is load-bearing.** The proactive-reply pipeline
   (`src/proactive.py`, `proactive_scan.yml`, `approve_pending_reply.yml`,
   `pending_replies.json`) must stay isolated from the daily Curator/Mentor path: **no
   imports from `main.py` into `src/proactive.py`**, no shared state file, no code path
   where disabling Phase 4b breaks the daily post. Flag any wiring that tangles them — the
   fix is to refactor the shared piece into `src/utils.py`, not to cross the boundary.

6. **Outward actions stay human-gated.** Proactive replies never post unattended — the
   scan only *stages* drafts; a human approves via manual `workflow_dispatch`. The reply
   generator must be able to return `None`/SKIP and must validate voice + ground claims
   (no fabricated CVE numbers, versions, stats). Flag any change that would auto-post a
   reply or accept an unverifiable specific.

7. **Two-platform symmetry, no per-platform divergence of intent.** Same content to
   Bluesky and Mastodon (a length cap may force a trim, never different intent). Partial
   delivery must be handled cleanly (`*_partial_delivery`), never re-send already-posted
   thread parts.

8. **Tests and lint are the floor.** `ruff` clean and `pytest` green are required. New
   behaviour — especially defensive and failure paths — needs tests. The strongest
   reviews here have caught missing-edge-case handling (e.g. a required field that wasn't
   validated, letting a bad path silently fall through). That's the bar.

## How to review

- Be a sharp sparring partner. Flag real correctness holes and principle violations, not
  style nits. Distinguish severity honestly.
- **Verify against the code before asserting.** Quote the line. If the concern is
  hypothetical, say so.
- Prefer one well-grounded finding over five speculative ones.
- It is encouraged to be adversarial about correctness and about the principles above —
  that independence is the whole point of the review.

## Build / test

```
pip install -r requirements.txt
ruff check src/ main.py scripts/ tests/
pytest -q
```

`requirements.txt` is generated from `requirements.in` via `pip-compile` (the lockfile
check in CI enforces they match). Add new deps to `requirements.in`, including dev tools
(`ruff`, `pytest`), or the lockfile regen drops them.
