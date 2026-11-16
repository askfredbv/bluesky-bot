# AGENTS.md — working in (and reviewing) this repo

Guidance for any AI agent authoring or reviewing changes here (Codex, Claude, etc.).
Read this before reviewing a PR: flag violations of the guardrails below, not just
generic bugs. Ground every flag in the actual code — verify the claim holds before
asserting it (a reviewer can be wrong too).

## What this project is

A bot that posts AI/tech content to **Bluesky** (@askfred.be) and **Mastodon**,
twice daily via GitHub Actions (triggered by cron-job.org). Python 3.11, a two-provider
model chain for generation (`claude-sonnet-5-5` primary since 2026-10-12, the Gemini
chain as fallback — see `ANTHROPIC_CHAIN_POSITION` in `src/config.py`; images stay
Gemini-only), a private GitHub Gist for state. **The voice is the product.** Read
`docs/PLAN_2027.md` before proposing anything: it is the direction, and it wins over
older docs. `docs/BACKLOG.md` is the open queue; `docs/BACKLOG_ARCHIVE.md` and
`docs/RETRO_2026-05-08.md` are history to mine, not limits.

**Operating goal (2026-11-16): purposeful growth and professionalism.** Reach the people
this perspective is for, without giving in on reliability, trust or professionalism. Not
growth for the sake of growth. Canonical text and history: `docs/BACKLOG.md` §1.

## Review priorities — the six guardrails

This section is mirrored, for the one reviewer that cannot read this file, in
`.gemini/styleguide.md` (Gemini Code Assist reads only its own `.gemini/` directory).
**This file is canonical; change the two in the same commit** — a divergent copy of a
review rule is worse than no copy, because each one then looks authoritative.
`tests/test_review_rule_mirror.py` fails if the two stop listing the same priorities in
the same order. It compares titles only: it cannot see a rule sharpened here while the
mirror keeps the old wording, so the discipline above is still yours to keep.

The six guardrails are `docs/PLAN_2027.md`'s, word for word. Every other rule has to earn
its place against them. A check listed under a guardrail is there because an incident
put it there; the default answer to a new direction is "yes, with a check date".

1. **True to the source.** A post claims only what its source says, with no invented numbers, versions or quotes.

   - The 2026-11-05 morning post turned Krebs's hedged lede into flat facts; every voice
     validator passed it and Frederik removed it by hand (#248). Flag a change that
     weakens or bypasses the Curator grounding check.

2. **Positive and useful.** No negativity, no clickbait, no politics. A strong, specific point is welcome: surprise comes from the fact, not from the tone.

3. **Skip rather than embarrass.** When a post fails the source check or the voice check, skip the run. Taste is judged at the monthly read, not by blocking runs.

   - v4.18 shipped a hardcoded `"Notes on {topic} — more soon."` placeholder for weeks
     (`docs/RETRO_2026-05-08.md`). On generation failure, return empty and skip the
     broadcast. Flag any path that produces postable text without passing
     `_apply_voice_trim` and `validate_summary` (`src/agents.py`).

4. **The replies are Frederik's.** The bot never answers people on its own.

   - Until 2026-11-16 the bot answered mentions automatically; that was switched off
     (#273). Flag any path that posts a reply without a human approving it.

5. **One account, one voice.** Bluesky and Mastodon get the same post. Only the mechanics differ.

   - Mechanics: the source as a link card on Bluesky and a URL on Mastodon
     (`ensure_mastodon_source_link`), Mastodon-only discovery tags (`apply_mastodon_tags`)
     and thread-position markers (`number_mastodon_thread`, since Mastodon showed 2/2
     above 1/2, #160). Divergence lives in the broadcaster, never in the prompt.
     Anything appended after `validate_summary` bypasses the validators, so it may not
     add a claim or framing the post does not carry. Flag one that does.

6. **Live is the proof.** A change is done when the public feed shows it working, not when CI is green.

   - Post images carried rendered captions for six months while ruff, mypy, pytest and
     CodeQL were green; opening three images found it (v4.31.0). Flag a PR that calls
     itself done on CI alone. What "live" means is the next section.

## What "live" means here

This bot has no staging. A change is live when **the next scheduled run on `main` has
used it, and the post it produced reads correctly on the public Bluesky and Mastodon
APIs** (not in the Actions log, which shows what was sent, not what readers see).
A green CI run or a merged PR is not live. A change that only affects a path not taken
on every run (a fallback, a thread, a Curator pick) is live-verified only once a run has
actually taken that path; until then say "merged, not yet exercised".

## How to review

- Be a sharp sparring partner. Flag real correctness holes and principle violations, not
  style nits. Distinguish severity honestly.
- **Verify against the code before asserting.** Quote the line. If the concern is
  hypothetical, say so.
- Prefer one well-grounded finding over five speculative ones.
- It is encouraged to be adversarial about correctness and about the principles above —
  that independence is the whole point of the review.

## Build / test

The fast loop while working:

```
pip install -r requirements-dev.txt
ruff check src/ main.py scripts/ tests/
mypy
pytest -q
```

**All three, not two.** CI runs `mypy` as its own gate, and `ruff` + `pytest`
green is not the same as CI green: on 2026-10-14 a PR went up with both passing
and the type-check red, because this block listed only two commands and the
agent working from it never ran the third. `mypy` takes no arguments on
purpose: scope and flags live in `[tool.mypy]` in `pyproject.toml` precisely so
a bare local run checks what the gate checks.

Before pushing, the coverage gate, which the fast loop does not reproduce:

```
pytest --cov=main --cov=src --cov-fail-under=80
```

`pytest -q` passes while coverage sits below the line, so new code with no tests
clears the fast loop and fails CI.

Only if you edited `pyproject.toml`, regenerate both lockfiles with the exact
flags the check uses, then confirm `git diff` is empty for them:

```
pip-compile --quiet --no-header --no-annotate --strip-extras --output-file requirements.txt pyproject.toml
pip-compile --quiet --no-header --no-annotate --strip-extras --extra dev --output-file requirements-dev.txt pyproject.toml
```

`requirements.txt` (runtime) and `requirements-dev.txt` (runtime + dev tools) are both
generated from `pyproject.toml` via `pip-compile` (the lockfile check in CI enforces they
match). Add new deps to `pyproject.toml` — runtime under `[project.dependencies]`, dev
tools (`ruff`, `pytest`, `mypy`, …) under `[project.optional-dependencies] dev` — then
regenerate both lockfiles.
