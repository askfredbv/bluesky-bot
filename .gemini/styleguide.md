# Review style guide — bluesky-bot

This file extends the standard Gemini Code Assist review prompt for this repository.
It mirrors the "Review priorities" section of [`AGENTS.md`](../AGENTS.md), which is the
canonical statement of them. **If you change one, change the other in the same commit.**

Keep the five default review areas (correctness, efficiency, maintainability, security,
testing). The sections below are what a generic reviewer misses here, and they are the
reason this file exists.

## What this project is

A bot that posts AI/tech-mentorship content to Bluesky (@askfred.be) and Mastodon, twice
daily via GitHub Actions. Python 3.11, a two-provider model chain for generation
(`claude-sonnet-5-5` primary since 2026-10-12, the Gemini chain as fallback — see
`ANTHROPIC_CHAIN_POSITION` in `src/config.py`; images stay Gemini-only), a private GitHub
Gist for state. It runs unattended and it posts under a real person's name.

**The voice is the product.** The operating goal (set 2026-10-06) is: run reliably, cost
no recurring human time, never embarrass its author. Audience growth is deprioritised.

Two consequences for review:

- **Do not propose growth, engagement-optimisation or reply-automation work, and do not
  justify a change by its effect on reach.** Engagement data is noise at this scale
  (68 posts, 42 interactions, 41 of them zero), so it supports no decision either way.
- **Prefer changes with no recurring human cost.** An experiment nobody reads is worse
  than no experiment. Voice and correctness still matter in full.

## Review priorities

### 1. Voice is a brand decision, not a metric decision

Content and prompt changes must preserve the voice rules: no hype words, no reader-bait
questions, no broken-promise teasers ("more soon", "stay tuned", 🧵), no source-summary
openers ("A new paper…", "Researchers found…", "Sobering read."), first-person presence
by default, and the two registers (strategic-advisory and casual-narrative) defined in
`STYLE_GUIDELINES` in `src/config.py`.

Flag any change that loosens these, **even if the result reads fine.** "It reads fine" is
not the bar; the bar is the rules above.

### 2. No hardcoded fallback content that bypasses voice/validation

A v4.18 bug shipped a hardcoded `"Notes on {topic} — more soon."` placeholder to
production for weeks. The rule that came out of it: **missing a run beats shipping
garbage.**

On generation failure the correct behaviour is to return empty and skip the broadcast.
Flag any new fallback that produces postable text without passing `_apply_voice_trim` and
`validate_summary` (both in `src/agents.py`).

### 3. Diagnostic discipline: capture the message, not just the type

Every `except` that logs must include `error_msg=str(e)[:200]` or equivalent, not
`error_type` alone.

Several multi-day debugging delays (a Step 2 `KeyError`, the Imagen 3 deprecation, the
`bluesky_session` revocation loop, a `gemini-2.5-pro` `AttributeError`) were caused by
logging the type without the message. Flag any new catch that logs the type alone.

### 4. Fail loud on critical state, fail safe on the rest

State-persistence failures (Gist writes) must log at ERROR, not WARN: silent degradation
is worse than noise. Best-effort work (the proactive scan, a metrics refresh, a single
feed fetch) should catch, log and continue rather than crash the run.

Flag the wrong choice in **either** direction. Over-defensive is a finding here, not a
virtue.

### 5. Phase 4b kill-switch separation is load-bearing

The proactive-reply pipeline (`src/proactive.py`, `proactive_scan.yml`,
`approve_pending_reply.yml`, `pending_replies.json`) must stay isolated from the daily
Curator/Mentor path. Specifically: **no imports from `main.py` into `src/proactive.py`**,
no shared state file, and no code path where disabling Phase 4b breaks the daily post.

Flag any wiring that tangles them. The fix is to move the shared piece into a shared
module under `src/` (state I/O lives in `src/state_store.py`), never to cross the
boundary.

### 6. Outward actions stay human-gated

Proactive replies never post unattended: the scan only *stages* drafts, and a human
approves via a manual `workflow_dispatch`. The reply generator must be able to return
`None`/SKIP, and must validate voice and ground its claims (no fabricated CVE numbers,
versions or statistics).

Flag any change that would auto-post a reply, or that would accept an unverifiable
specific.

### 7. Two-platform symmetry, no per-platform divergence of intent

Same content to Bluesky and Mastodon. A platform's *mechanics* may differ: a length trim,
embed shape (the Curator's source rides as a link card on Bluesky and as a URL in the
Mastodon text, `ensure_mastodon_source_link`), Mastodon-only discovery hashtags
(`apply_mastodon_tags`, because Mastodon has no algorithmic feed and a followed tag is
its discovery mechanism), or Mastodon-only thread-position markers
(`number_mastodon_thread`, because Mastodon lists a self-thread newest-first).

A platform's *intent* may not differ: no different take, link or framing, no
one-platform voice rule. Generation stays platform-neutral, so divergence lives in
`src/broadcasters.py`, never in the prompt. Partial delivery must be handled cleanly
(`*_partial_delivery`) and must never re-send an already-posted thread part.

**"Mechanics" is not an exemption from editorial scrutiny.** Anything appended after
`validate_summary` bypasses the voice validators by construction, so the bar it must
clear is stated here instead:

- A discovery tag describes the subject the post already has. It may not introduce a
  claim, an endorsement, a product affiliation, or a framing the prose does not carry.
- It spends the shared `MAX_HASHTAGS_PER_POST` ceiling rather than adding a second
  allowance on top of it. A Mastodon post must never carry more hashtags than the same
  post on Bluesky is allowed.
- A thread-position marker carries no content: it states a part's place in a thread that
  is already complete when posted, which is why it is not the teaser §1 bans.

Flag any appended-after-validation content that cannot meet that bar, and any new
per-platform mechanic that widens what the reader sees rather than adapting how they
find it.

### 8. Tests and lint are the floor

`ruff` clean, `mypy` clean and `pytest` green are required, and coverage is gated at 80%
(`pytest --cov=main --cov=src --cov-fail-under=80`).

New behaviour — especially defensive and failure paths — needs tests. The strongest
reviews on this repo have caught missing edge cases: a required field that was not
validated, a bad path that silently fell through. That is the bar.

Dependencies are declared in `pyproject.toml`; `requirements.txt` and
`requirements-dev.txt` are generated from it by `pip-compile` and are not reviewed here.
Flag a dependency added directly to a lockfile.

## How to review

- Be a sharp sparring partner. Flag real correctness holes and principle violations, not
  style nits. `ruff` already owns formatting and lint; a comment that repeats it is noise.
- **Verify against the code before asserting.** Quote the line you mean. If a concern is
  hypothetical, say so in the comment.
- **Distinguish severity honestly.** Prefer one well-grounded finding over five
  speculative ones. A reviewer can be wrong too.
- Being adversarial about correctness and about the principles above is encouraged. That
  independence is the whole point of the review.
