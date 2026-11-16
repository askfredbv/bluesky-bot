# Review style guide — bluesky-bot

This file extends the standard Gemini Code Assist review prompt for this repository.
It mirrors the "Review priorities" section of [`AGENTS.md`](../AGENTS.md), which is the
canonical statement of them. **If you change one, change the other in the same commit.**

Keep the five default review areas (correctness, efficiency, maintainability, security,
testing). The sections below are what a generic reviewer misses here, and they are the
reason this file exists.

## What this project is

A bot that posts AI/tech content to Bluesky (@askfred.be) and Mastodon, twice
daily via GitHub Actions. Python 3.11, a two-provider model chain for generation
(`claude-sonnet-5-5` primary since 2026-10-12, the Gemini chain as fallback — see
`ANTHROPIC_CHAIN_POSITION` in `src/config.py`; images stay Gemini-only), a private GitHub
Gist for state. It runs unattended and it posts under a real person's name.

**The voice is the product.** The operating goal (set 2026-11-16) is: purposeful growth
and professionalism. Reach the people this perspective is for, without giving in on
reliability, trust or professionalism. Not growth for the sake of growth.

The direction is `docs/PLAN_2027.md`. Older docs are history to mine, not limits.

## Review priorities

The six guardrails of `docs/PLAN_2027.md`, word for word. Every other rule has to earn its
place against them. A check listed under a guardrail is there because an incident put it
there; the default answer to a new direction is "yes, with a check date".

### 1. True to the source

A post claims only what its source says, with no invented numbers, versions or quotes.

The 2026-11-05 morning post turned Krebs's hedged lede into flat facts; every voice
validator passed it and Frederik removed it by hand (#248). Flag a change that weakens or
bypasses the Curator grounding check.

### 2. Positive and useful

No negativity, no clickbait, no politics. A strong, specific point is welcome: surprise comes from the fact, not from the tone.

### 3. Skip rather than embarrass

When a post fails the source check or the voice check, skip the run. Taste is judged at the monthly read, not by blocking runs.

v4.18 shipped a hardcoded `"Notes on {topic} — more soon."` placeholder for weeks. On
generation failure, return empty and skip the broadcast. Flag any path that produces
postable text without passing `_apply_voice_trim` and `validate_summary`
(`src/agents.py`).

### 4. The replies are Frederik's

The bot never answers people on its own.

Until 2026-11-16 the bot answered mentions automatically; that was switched off (#273).
Flag any path that posts a reply without a human approving it.

### 5. One account, one voice

Bluesky and Mastodon get the same post. Only the mechanics differ.

Mechanics: the source as a link card on Bluesky and a URL on Mastodon
(`ensure_mastodon_source_link`), Mastodon-only discovery tags (`apply_mastodon_tags`) and
thread-position markers (`number_mastodon_thread`, since Mastodon showed 2/2 above 1/2,
#160). Divergence lives in `src/broadcasters.py`, never in the prompt. Anything appended
after `validate_summary` bypasses the validators, so it may not add a claim or framing
the post does not carry. Flag one that does.

### 6. Live is the proof

A change is done when the public feed shows it working, not when CI is green.

Post images carried rendered captions for six months while ruff, mypy, pytest and CodeQL
were green; opening three images found it (v4.31.0). Flag a PR that calls itself done on
CI alone.

**What "live" means here** (verbatim from `AGENTS.md`): This bot has no staging. A change is live when **the next scheduled run on `main` has
used it, and the post it produced reads correctly on the public Bluesky and Mastodon
APIs** (not in the Actions log, which shows what was sent, not what readers see).
A green CI run or a merged PR is not live. A change that only affects a path not taken
on every run (a fallback, a thread, a Curator pick) is live-verified only once a run has
actually taken that path; until then say "merged, not yet exercised".

`ruff`, `mypy` and `pytest` green, with coverage at 80%, are still the floor
CI enforces.

## How to review

- Be a sharp sparring partner. Flag real correctness holes and principle violations, not
  style nits. `ruff` already owns formatting and lint; a comment that repeats it is noise.
- **Verify against the code before asserting.** Quote the line you mean. If a concern is
  hypothetical, say so in the comment.
- **Distinguish severity honestly.** Prefer one well-grounded finding over five
  speculative ones. A reviewer can be wrong too.
- Being adversarial about correctness and about the principles above is encouraged. That
  independence is the whole point of the review.
