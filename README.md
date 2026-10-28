# Bluesky & Mastodon Daily Poster (v4.31.0)

![Daily Post](https://github.com/askfredbv/bluesky-bot/actions/workflows/daily_post.yml/badge.svg)

An automated bot that posts to **Bluesky** (@askfred.be) and **Mastodon** — twice a day, two different modes. Most posts are a single short post; a story that needs more room becomes a short thread, almost always two parts.

The afternoon run also runs a **pioneer dimension** — curated tech-history facts that fire on anniversaries or probabilistically (~2–3 posts/week). The bar: things a working dev would say "huh, didn't know that" to.

---

## What it does

**Morning run — The Curator.** Fetches AI/tech RSS feeds, scores items by source quality, recency, and topic diversity, then writes about the most consequential development — the "…which means" that follows the headline, not the headline itself. One post by default; 2–3 only if the story needs it.

**Afternoon run — The Mentor and The Strategist, alternating.** A specific, observational take: the kind of thing that is obvious in hindsight but rarely articulated. The two alternate on day parity, drawing from separate topic pools — the Mentor on working life, change and adoption (seeded from Frederik's own articles on frederikvanhecke.com and askfred.be, with a handful of AI-practice topics), the Strategist on the longer horizon ("what does this look like in five years"). Either may instead run a Pioneer post about a figure or moment in computing history. The Strategist also stands in for a morning Curator run when news volume is too low to post anything worth reading.

**Voice** — first-person, dry, no hype, no reader-bait questions, no day-of-week openers. Default zero hashtags, max two when they're a clear topic anchor.

---

## How it works

```
cron-job.org → GitHub Actions (workflow_dispatch)
  → fetch + score RSS feeds     (src/news.py)
  → generate via model chain    (src/agents.py → src/llm.py)
  → post to Bluesky + Mastodon  (src/broadcasters.py)
  → reply to mentions           (src/agents.py)
```

Two runs a day, 09:00 and 19:00 Belgian local time (UTC shifts with DST: winter 08:00/18:00, summer 07:00/17:00). A read-only `schedule-health.yml` monitor flags any 24h window that received fewer than the expected 2 external dispatches.

**Under the hood:**

- **Scoring** — source tier (matched on the link's hostname, exact or subdomain), product-launch signals, a momentum bonus for flagship models, technical-depth keywords, bounded time decay, a topic-repetition cooldown that decays with recency, and a consensus bonus for cross-source stories. Keyword signals match whole words, not substrings.
- **Candidate mix** — the shortlist handed to the model is source-mixed, not just top-scored: research sources (arXiv et al) have both a reserved floor and a cap, and primary sources (an AI lab's own blog) have a best-effort floor. The model writes about one item from that shortlist, so the shortlist's job is to be a representative menu rather than a winner.
- **Voice** — anchored to Frederik Van Hecke's writing in two registers (strategic-advisory / casual-narrative); verbatim anchors live in `src/config.py`, and `docs/VOICE_AUDIT.md` documents the independent voice-critique process. English only.
- **Curator: a take, not a summary** — three-part shape (hook → concrete specific → link); paper-summary phrasings and editorial-filler endings are banned.
- **No broken promises** — the bot has no follow-up mechanism, so teasers ("more soon", 🧵) are banned and stripped; every post lands complete on its own.
- **Images** — Mentor/Strategist posts may attach an AI-generated illustration (`gemini-3.1-flash-image`; two-step — Gemini drafts the visual prompt, then the image model renders it). Both steps stay on Gemini even while Claude writes the text, so a Google outage costs the image but not the post. Curator uses an RSS link card instead.
- **Mastodon discovery tags** — the Mastodon copy gets 1–2 trailing hashtags on its root post; the Bluesky copy gets none. Mastodon has no algorithmic feed, so a followed hashtag is how people find posts there; Bluesky's Discover feed does that job instead. Not a voice change: the post is generated once, platform-neutral, and the tags are appended at the broadcaster. A model names them per-post, a **different** model reviews them against the post, and the pair runs concurrently with the Bluesky broadcast inside a shared 20s budget — every failure path ships the post untagged. Kill switch: `MASTODON_TAGS_ENABLED`.
- **Mastodon thread numbering** — multi-part posts carry "1/2", "2/2" on Mastodon. Mastodon lists a self-thread newest-first, so a timeline reader meets part 2 before part 1. The first part ends with "1/2"; every later part opens with its number ("2/2: …"), so the part a reader meets first says at once that it continues something. Bluesky's app already numbers self-threads on its own, so the Bluesky copy is left as it is. Added at the broadcaster, before the discovery tags; the generated text is unchanged.
- **Mastodon source link** — Curator posts carry their source URL in the Mastodon text whenever the generated text does not already include it. Bluesky shows the same link as a card built from the article's metadata; Mastodon can only show a link that is in the text, and without this most Curator posts reached Mastodon with no source at all.
- **Models** — one ordered failover chain spanning two providers: `ANTHROPIC_MODELS` (`claude-sonnet-5-5` → `claude-haiku-5-5`) and `GEMINI_MODEL_PRIORITY` (`gemini-3.7-flash` → `gemini-3.5-flash` → `gemini-2.5-pro` → `gemini-2.5-flash`). Which provider leads is the single constant `ANTHROPIC_CHAIN_POSITION`, **`"primary"` since 2026-10-12** — so Claude writes the posts and the Gemini chain is the fallback. That began as a two-week trial and was kept on 2026-10-26 (`docs/BACKLOG.md`); setting the constant to `"fallback"` restores Gemini-first with no other change. With no `ANTHROPIC_API_KEY` configured the chain is Gemini-only. An API failure advances to the next model, and the Gemini rungs are pruned at startup to what the key can actually reach. Thinking budget is pinned per model so it doesn't eat the output budget.
- **Mention replies** — a mention from the last 48 hours (`MENTION_MAX_AGE_HOURS`) that has not been answered yet gets a reply, at most 10 per run. Replies walk the same model chain as the posts, so Sonnet writes them and the Gemini rungs are the fallback; if every model fails, the mention stays unanswered and the next run tries again. A mention inside someone else's thread is answered in that thread. Mentions are picked by age, not by "unread", because opening the Bluesky app marks them read.
- **Reliability** — concurrent delivery (one platform failing doesn't block the other); per-thread shared retry budgets for rate-limit + transient errors with partial-delivery semantics (no re-sending posts already on the wire); Gist-backed state with local-file fallback; a cached Bluesky session string to skip warm-run logins; a fully exhausted model chain skips the run cleanly (no placeholder posts).
- **Telemetry** — three Gist JSON files: `post_metrics.json` (per-post engagement + formatting features, refreshed ~daily), `feed_health.json` (per-feed fetch health, rolling window), `growth.json` (follower snapshots). All three are recorded for observability, not as optimisation targets: audience growth is deprioritised (see `AGENTS.md`, "What this project is"), and at this scale the engagement numbers are noise rather than signal — nothing in the pipeline reads them back into a decision. A configured feed flagged as broken or stale opens a GitHub issue labelled `feed-health`, which closes itself once every feed is healthy again.

---

## Setup

### Environment Variables

Add these as GitHub repo secrets (`Settings > Secrets and variables > Actions`):

| Secret | Required | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Yes | Google AI Studio key. Images always — Anthropic cannot generate them. Text only when the chain falls through to Gemini, or for every post if `ANTHROPIC_API_KEY` is unset |
| `BLUESKY_USERNAME` | Yes | Your Bluesky handle (e.g. `yourname.bsky.social`) |
| `BLUESKY_APP_PASSWORD` | Yes | App-specific password from Bluesky settings |
| `MASTODON_ACCESS_TOKEN` | Optional | Access token from your Mastodon instance |
| `MASTODON_API_BASE_URL` | Optional | Your Mastodon instance URL (the API base, e.g. `https://mastodon.social`) |
| `ANTHROPIC_API_KEY` | Optional, but **load-bearing since 2026-10-12** | `ANTHROPIC_CHAIN_POSITION` is `"primary"`, so Anthropic writes the posts and the mention replies, and Gemini is the fallback. Unset or revoked is still never a failed run: the chain silently reverts to Gemini-only and keeps posting in the old voice, which means a dead key is invisible except in the `model_used` log line. Use a **workspace-scoped** key, never an org admin key. |
| `GIST_TOKEN` | Yes | GitHub PAT with `gist` scope — for persistent state |
| `GIST_ID` | Yes | ID of the private Gist holding the state files |

### Local run

```bash
pip install -r requirements.txt
python main.py
```

Create a `.env` from `.env.example` for local credentials.

### Tests & coverage

```bash
pytest
```

CI enforces a single **global 80% coverage floor** across `main.py` and `src/` (`pytest --cov-fail-under=80`), alongside `ruff` and a whole-codebase `mypy` gate. A separate `dependency-audit.yml` workflow checks the runtime lockfile for known vulnerabilities (`pip-audit`) on every PR, on pushes to `main`, and daily.

### Dependency management

Dependencies are declared in `pyproject.toml` — runtime under `[project.dependencies]`, dev tools like `ruff`/`pytest`/`mypy` under the `dev` optional-dependency group — and pinned to two lockfiles via `pip-tools`. CI fails if either lockfile is out of sync:

```bash
pip install pip-tools
pip-compile --no-header --no-annotate --strip-extras --output-file requirements.txt pyproject.toml
pip-compile --no-header --no-annotate --strip-extras --extra dev --output-file requirements-dev.txt pyproject.toml
```

**Dependabot** proposes one grouped PR a week (Monday 07:00 Brussels) with every minor and patch bump, and only for releases at least seven days old. Its lockfile edits never quite match `pip-compile`, so `dependabot-lockfile.yml` regenerates both lockfiles on the Dependabot PR and pushes them back, which makes the PR pass `lockfile` by construction. Majors stay a human decision: they mean raising a ceiling in `pyproject.toml`. Security updates skip both the group and the wait.

Production installs `requirements.txt` (runtime only); CI and local dev install `requirements-dev.txt` (runtime + tooling).

Platform note: file locks use `fcntl.flock` on Linux/macOS, `msvcrt.locking` on Windows.

---

## Customisation

The main levers are in `src/config.py`:

- **`RSS_FEEDS`** / **`SOURCE_TIERS`** — feeds and their per-domain relevance weights
- **`SECONDARY_TOPICS`** / **`MENTOR_TOPICS`** — topic pools for Mentor and Strategist modes
- **`SYSTEM_INSTRUCTIONS_CURATOR` / `SYSTEM_INSTRUCTIONS_MENTOR`** — the voice and instructions sent to the model
- **`LANGUAGE_OPTIONS`** — languages the model may pick from (default `["English"]`)
- **`IMAGE_MODEL`** — image model (default `gemini-3.1-flash-image`)
- **`IMAGE_GENERATION_PROBABILITY`** — chance of a *generated* image (default `0.85`). Applies to the Mentor/Strategist post image and to the Curator fallback card image used when an article has no usable OG thumbnail; it never strips an article's own thumbnail.
- **`GEMINI_MODEL_PRIORITY`** — the Gemini rungs of the failover chain (pruned to available models at startup; Gemma models get inlined prompts automatically)
- **`ANTHROPIC_MODELS`** — the Anthropic rungs, in preference order (default `claude-sonnet-5-5` → `claude-haiku-5-5`)
- **`ANTHROPIC_CHAIN_POSITION`** — whether those rungs sit `"primary"` (above the Gemini chain) or `"fallback"` (below it). Currently `"primary"`; this one word is the revert switch, kept as `"primary"` after the 2026-10-26 trial decision in `docs/BACKLOG.md`
- **`MOMENTUM_PRODUCTS` / `MOMENTUM_PRODUCT_BONUS`** — flagship model names worth a scoring bonus; refreshed monthly by `refresh_momentum.yml`, which opens a PR rather than pushing
- **`CONSENSUS_SYNERGY_BONUS`** — score bonus per additional feed covering the same story

Override without touching code via env vars: `POST_JITTER_MIN_SECONDS` / `POST_JITTER_MAX_SECONDS` (pre-post delay; `30`/`300` in Actions).

---

## Project structure

```
.
├── main.py                 # Async orchestrator & pipeline stages
├── src/
│   ├── agents.py           # Content generation, image prompts, mention handling
│   ├── llm.py              # Provider seam: routes text generation to Anthropic or Gemini
│   ├── broadcasters.py     # Bluesky + Mastodon posting, per-post retry, Mastodon-only mechanics
│   ├── bluesky_session.py  # Session-string caching via Gist
│   ├── proactive.py        # Phase 4b proactive replies (dormant; human-gated)
│   ├── config.py           # Constants, personas, feeds, scoring, prompts
│   ├── facets.py           # Bluesky rich-text facets (clickable URLs + hashtags)
│   ├── metrics.py          # Telemetry: post_metrics / feed_health / growth
│   ├── net_safety.py       # SSRF-guarded fetching: public-IP allowlist, DNS pinning, safe redirects, size cap
│   ├── news.py             # RSS fetch, relevance scoring, cross-publisher consensus clustering
│   ├── retry.py            # Rate-limit vs transient classification + backoff budgets
│   ├── state_store.py      # State I/O: Gist / remote store / local atomic writes (seen, replied-to)
│   ├── utils.py            # OG-metadata scrape, the shared image compressor, Pillow bomb cap
│   └── ...                 # file_lock, logger, settings
├── scripts/                # One-shot tools: watchlist audit, model discovery, voice audit
├── tests/                  # pytest suite
├── docs/                   # PLAN, BACKLOG, RELEASING (release checklist), retros, VOICE_AUDIT
├── .github/workflows/      # daily_post, schedule-health, tests, lockfile-check, dependabot-lockfile,
│                           #   codeql, dependency-audit, workflow-lint, image-audit, refresh_momentum,
│                           #   voice-audit, the manual probes (model-discovery, image-probe,
│                           #   mastodon-tag-probe, fallback-probe), and proactive_scan +
│                           #   approve_pending_reply (dormant)
├── AGENTS.md               # Briefs the Codex PR reviewer on project principles
├── pyproject.toml          # Dependency constraints (runtime + dev extra) + project metadata
├── requirements.txt        # Pinned runtime lockfile (generated)
└── requirements-dev.txt    # Pinned runtime + dev lockfile (generated)
```

---

## License

MIT. Built by [Frederik Van Hecke](https://askfred.be).
