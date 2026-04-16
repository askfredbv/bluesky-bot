# Bluesky & Mastodon Daily Poster (v4.8.0)

An automated bot that posts daily threads to **Bluesky** (@askfred.be) and **Mastodon** — twice a day, two different modes.

---

## What it does

**Morning run (09:00 UTC) — The Curator**
Fetches from 17 AI/tech RSS feeds, scores items by source quality, recency, and topic diversity, then generates a 3–5 post thread on the most consequential developments. The goal is the "...which means" that follows the headline, not the headline itself.

**Afternoon run (15:30 UTC) — The Mentor**
Picks a career or work-life topic and writes a short thread. The target is specific, observational advice — the kind that is obvious in hindsight but rarely articulated.

If the morning news volume is low (fewer than 3 high-signal items), the bot falls back to a longer-horizon "strategist" take on a secondary topic rather than posting nothing.

---

## How it works

```
cron-job.org → GitHub Actions (workflow_dispatch)
  → fetch RSS feeds + score items          (src/utils.py)
  → generate thread via Gemini              (src/agents.py)
  → post to Bluesky + Mastodon in parallel (src/broadcasters.py)
  → check and reply to mentions            (src/agents.py)
```

### Schedule reliability monitor

To improve observability of the external trigger path, `.github/workflows/schedule-health.yml` runs daily (and can also be run manually) and checks whether `.github/workflows/daily_post.yml` received the expected number of `workflow_dispatch` runs in the previous 24 hours.

- Expected dispatches: **2 per 24h window** (matching the two cron-job.org triggers)
- Scope: **read-only operational check** (no posting, no content generation)
- Failure mode: the monitor workflow fails if dispatch count is below expectation so maintainers can quickly spot missed trigger windows in Actions history.

**Content scoring** uses six factors: source tier, product-launch signals, technical depth keywords, time decay (0.5 pts/hour), a topic diversity penalty to avoid repetition, and a Consensus Synergy bonus (+1.5 per additional feed) for stories covered by multiple independent sources. arXiv papers get priority injection if they don't survive the scoring on their own.

**Voice** is anchored to Frederik Van Hecke's writing style — direct, pragmatic, dry. No hype language. No corporate throat-clearing. Short punchy sentences mixed with longer ones. The system prompts in `src/config.py` include verbatim examples from his writing as style anchors for the model.

**Post formatting**: each post in a thread is generated within the 300-character Bluesky limit. If a post still overflows (rare), it is split at a word boundary — never mid-word.

**Reliability**: concurrent platform delivery with `asyncio.gather`; if Mastodon fails, Bluesky still posts. Exponential backoff on API calls. Atomic JSON writes with backup/restore for state files — corrupt files are preserved with a timestamped name before resetting to default. Hard cap of 10 mention replies per run. Content generation uses a model priority list (`gemini-2.5-flash` → `gemini-2.0-flash` → `gemini-1.5-flash-latest` → `gemma-3-27b-it`); API-level failures (quota, unavailability) automatically advance to the next model.

---

## Setup

### Environment Variables

Add these as GitHub repo secrets (`Settings > Secrets and variables > Actions`):

| Secret | Required | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Yes | Google AI Studio key for content generation |
| `BLUESKY_USERNAME` | Yes | Your Bluesky handle (e.g. `yourname.bsky.social`) |
| `BLUESKY_APP_PASSWORD` | Yes | App-specific password from Bluesky settings |
| `MASTODON_ACCESS_TOKEN` | Optional | Access token from your Mastodon instance |
| `MASTODON_API_BASE_URL` | Optional | Your Mastodon instance URL |

### Local run

```bash
pip install -r requirements.txt
python main.py
```

Create a `.env` file from `.env.example` for local credentials.

### Tests

```bash
pytest
```

### Dependency management

Dependencies are pinned via `pip-tools`. Edit intent in `requirements.in`, then regenerate:

```bash
pip install pip-tools
pip-compile --no-header --no-annotate --strip-extras --output-file requirements.txt requirements.in
```

CI fails if `requirements.txt` is out of sync with `requirements.in`.

### Platform notes

- **Linux / macOS**: file locks use `fcntl.flock`
- **Windows**: file locks use `msvcrt.locking`

---

## Customisation

The main levers are all in `src/config.py`:

- **`RSS_FEEDS`** — add or remove feeds
- **`SOURCE_TIERS`** — adjust per-domain relevance weights
- **`SECONDARY_TOPICS`** — the topic pool for Mentor and Strategist modes
- **`SYSTEM_INSTRUCTIONS_CURATOR` / `SYSTEM_INSTRUCTIONS_MENTOR`** — the voice and instructions sent to the model
- **`APPROVED_BIO_BSKY` / `APPROVED_BIO_MASTODON`** — profile bios, refreshed on a weekly cooldown
- **`MENTION_SANITIZE_MAX_CHARS`** / **`FEED_SUMMARY_MAX_CHARS`** — character caps for mention input and RSS feed summaries (both default 500)
- **`CONSENSUS_SYNERGY_BONUS`** — score bonus per additional feed that covers the same story (default 1.5)
- **`GEMINI_MODEL_PRIORITY`** — ordered list of models to try; first API-level failure advances to the next

The following can also be overridden via environment variables without touching code:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `POST_JITTER_MIN_SECONDS` | `120` | Minimum pre-post delay (set to `30` in Actions) |
| `POST_JITTER_MAX_SECONDS` | `1800` | Maximum pre-post delay (set to `300` in Actions) |

---

## Project structure

```
.
├── main.py                    # Async orchestrator & pipeline stages
├── src/
│   ├── agents.py              # Content generation and mention handling
│   ├── broadcasters.py        # Bluesky and Mastodon posting
│   ├── config.py              # Constants, personas, feeds, scoring config
│   ├── file_lock.py           # Cross-platform file locking
│   ├── logger.py              # SafeLogger — strips credentials from output
│   ├── settings.py            # Environment variable loading and validation
│   └── utils.py               # RSS fetching, scoring, metadata scraping, state I/O
├── tests/                     # pytest suite (76 tests)
├── .github/
│   ├── workflows/daily_post.yml      # workflow_dispatch target; triggered by cron-job.org at 08:00 and 14:30 UTC
│   ├── workflows/schedule-health.yml # Read-only daily monitor for missed external dispatches
│   ├── workflows/lockfile-check.yml  # Fails if requirements.txt is stale
│   └── dependabot.yml                # Dependency vulnerability scanning
├── pytest.ini
├── requirements.in            # Human-edited dependency constraints
└── requirements.txt           # Fully resolved lockfile
```

---

## License

MIT. Built by [Frederik Van Hecke](https://askfred.be).
