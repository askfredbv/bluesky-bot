# Bluesky & Mastodon Daily Poster (v4.22.0)

![Daily Post](https://github.com/askfredbv/bluesky-bot/actions/workflows/daily_post.yml/badge.svg)

An automated bot that posts to **Bluesky** (@askfred.be) and **Mastodon** — twice a day, two different modes. Default shape is a single short post; threads are reserved for the rare cases that genuinely need them.

The afternoon run also runs a **pioneer dimension** — curated tech-history facts that fire on anniversaries or probabilistically (~2–3 posts/week). The bar: things a working dev would say "huh, didn't know that" to.

---

## What it does

**Morning run — The Curator.** Fetches AI/tech RSS feeds, scores items by source quality, recency, and topic diversity, then writes about the most consequential development — the "…which means" that follows the headline, not the headline itself. One post by default; 2–3 only if the story needs it.

**Afternoon run — The Mentor.** A career or work-life observation: specific, observational, the kind of thing that is obvious in hindsight but rarely articulated. If morning news volume is low, a longer-horizon "strategist" take runs instead of posting nothing.

**Voice** — first-person, dry, no hype, no reader-bait questions, no day-of-week openers. Default zero hashtags, max two when they're a clear topic anchor.

---

## How it works

```
cron-job.org → GitHub Actions (workflow_dispatch)
  → fetch + score RSS feeds     (src/utils.py)
  → generate via Gemini         (src/agents.py)
  → post to Bluesky + Mastodon  (src/broadcasters.py)
  → reply to mentions           (src/agents.py)
```

Two runs a day, anchored to Belgian local time (UTC shifts with DST: summer 07:00/14:30, winter 08:00/15:30). A read-only `schedule-health.yml` monitor flags any 24h window that received fewer than the expected 2 external dispatches.

**Under the hood:**

- **Scoring** — source tier, product-launch signals, a momentum bonus for flagship models, technical-depth keywords, time decay, a topic-diversity penalty, and a consensus bonus for cross-source stories. arXiv papers get priority injection.
- **Voice** — anchored to Frederik Van Hecke's writing in two registers (strategic-advisory / casual-narrative); verbatim anchors live in `src/config.py`, and `docs/VOICE_AUDIT.md` documents the independent voice-critique process. English only.
- **Curator: a take, not a summary** — three-part shape (hook → concrete specific → link); paper-summary phrasings and editorial-filler endings are banned.
- **No broken promises** — the bot has no follow-up mechanism, so teasers ("more soon", 🧵) are banned and stripped; every post lands complete on its own.
- **Images** — Mentor/Strategist posts may attach an AI-generated illustration (`gemini-3.1-flash-image`; two-step — Gemini drafts the visual prompt, then the image model renders it). Curator uses an RSS link card instead.
- **Models** — a `GEMINI_MODEL_PRIORITY` chain (gemini-3.7-flash primary; gemini-3.5-flash the immediate fallback); an API failure advances to the next model, and the list is pruned to what the key can actually reach at startup. Thinking budget is pinned per model so it doesn't eat the output budget.
- **Reliability** — concurrent delivery (one platform failing doesn't block the other); per-thread shared retry budgets for rate-limit + transient errors with partial-delivery semantics (no re-sending posts already on the wire); Gist-backed state with local-file fallback; a cached Bluesky session string to skip warm-run logins; a fully exhausted model chain skips the run cleanly (no placeholder posts).
- **Telemetry** — three Gist JSON files: `post_metrics.json` (per-post engagement + formatting features, refreshed ~daily), `feed_health.json` (per-feed fetch health, rolling window), `growth.json` (follower snapshots — the success metric for building an audience).

---

## Setup

### Environment Variables

Add these as GitHub repo secrets (`Settings > Secrets and variables > Actions`):

| Secret | Required | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Yes | Google AI Studio key for content + image generation |
| `BLUESKY_USERNAME` | Yes | Your Bluesky handle (e.g. `yourname.bsky.social`) |
| `BLUESKY_APP_PASSWORD` | Yes | App-specific password from Bluesky settings |
| `MASTODON_ACCESS_TOKEN` | Optional | Access token from your Mastodon instance |
| `MASTODON_API_BASE_URL` | Optional | Your Mastodon instance URL (the API base, e.g. `https://mastodon.social`) |
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

CI enforces a **70% global coverage floor**, with **80% module gates** on the high-risk paths (`src/utils.py`, `src/agents.py`, `main.py`). PRs that change those modules must also update a matching test file.

### Dependency management

Dependencies are pinned via `pip-tools`. Edit intent in `requirements.in` (runtime **and** dev tools like `ruff`/`pytest`), then regenerate — CI fails if the lockfile is out of sync:

```bash
pip install pip-tools
pip-compile --no-header --no-annotate --strip-extras --output-file requirements.txt requirements.in
```

Platform note: file locks use `fcntl.flock` on Linux/macOS, `msvcrt.locking` on Windows.

---

## Customisation

The main levers are in `src/config.py`:

- **`RSS_FEEDS`** / **`SOURCE_TIERS`** — feeds and their per-domain relevance weights
- **`SECONDARY_TOPICS`** / **`MENTOR_TOPICS`** — topic pools for Mentor and Strategist modes
- **`SYSTEM_INSTRUCTIONS_CURATOR` / `SYSTEM_INSTRUCTIONS_MENTOR`** — the voice and instructions sent to the model
- **`LANGUAGE_OPTIONS`** — languages the model may pick from (default `["English"]`)
- **`IMAGE_MODEL`** — image model (default `gemini-3.1-flash-image`)
- **`IMAGE_GENERATION_PROBABILITY`** — chance of an image on Mentor/Strategist runs (default `0.5`)
- **`GEMINI_MODEL_PRIORITY`** — ordered model failover chain (pruned to available models at startup; Gemma models get inlined prompts automatically)
- **`MOMENTUM_PRODUCTS` / `MOMENTUM_PRODUCT_BONUS`** — flagship model names worth a scoring bonus; edited quarterly
- **`CONSENSUS_SYNERGY_BONUS`** — score bonus per additional feed covering the same story

Override without touching code via env vars: `POST_JITTER_MIN_SECONDS` / `POST_JITTER_MAX_SECONDS` (pre-post delay; `30`/`300` in Actions).

---

## Project structure

```
.
├── main.py                 # Async orchestrator & pipeline stages
├── src/
│   ├── agents.py           # Content generation, image prompts, mention handling
│   ├── broadcasters.py     # Bluesky + Mastodon posting (with image compression)
│   ├── bluesky_session.py  # Session-string caching via Gist
│   ├── proactive.py        # Phase 4b proactive replies (dormant; human-gated)
│   ├── config.py           # Constants, personas, feeds, scoring, prompts
│   ├── facets.py           # Bluesky rich-text facets (clickable URLs + hashtags)
│   ├── metrics.py          # Telemetry: post_metrics / feed_health / growth
│   ├── utils.py            # RSS fetch, scoring, metadata, state I/O, retry
│   └── ...                 # file_lock, logger, settings
├── scripts/                # One-shot tools: watchlist audit, model discovery, voice audit
├── tests/                  # pytest suite
├── docs/                   # PLAN, BACKLOG, retros, VOICE_AUDIT
├── .github/workflows/      # daily_post, schedule-health, lockfile-check, codeql,
│                           #   proactive_scan + approve_pending_reply (dormant), voice-audit, model-discovery
├── AGENTS.md               # Briefs the Codex PR reviewer on project principles
├── requirements.in         # Human-edited dependency constraints
└── requirements.txt        # Resolved lockfile
```

---

## License

MIT. Built by [Frederik Van Hecke](https://askfred.be).
