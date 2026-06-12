# Bluesky & Mastodon Daily Poster (v4.21.0)

![Daily Post](https://github.com/askfredbv/bluesky-bot/actions/workflows/daily_post.yml/badge.svg)

An automated bot that posts to **Bluesky** (@askfred.be) and **Mastodon** — twice a day, two different modes. Default shape is a single short post; threads are reserved for the rare cases that genuinely need them.

The afternoon run also runs a **pioneer dimension** — curated tech-history facts that fire on anniversaries or probabilistically (~3 posts/week). The bar: things a working dev would say "huh, didn't know that" to.

---

## What it does

**Morning run (07:00 UTC) — The Curator**
Fetches from 25 AI/tech RSS feeds, scores items by source quality, recency, and topic diversity, then writes about the most consequential development. Default is a single post; the model may extend to 2–3 if the story genuinely needs it. The goal is the "...which means" that follows the headline, not the headline itself.

**Afternoon run (14:30 UTC) — The Mentor**
Picks a career or work-life observation and writes a short post. The target is specific and observational — the kind of thing that is obvious in hindsight but rarely articulated. Defaults to one post; threads only when the observation can't fit.

**Voice (v4.14)** — first-person, dry, no day-of-week openers, no reader-bait questions, no hype words. Default zero hashtags, max two when they're a clear topic anchor.

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

**Content scoring** uses seven factors: source tier, product-launch signals, a **momentum product bonus** (+4.0) for flagship 2026 models (GPT-5, Claude 4, Llama 4, etc.), technical depth keywords, time decay (0.5 pts/hour), a topic diversity penalty to avoid repetition, and a Consensus Synergy bonus (+1.5 per additional feed) for stories covered by multiple independent sources. arXiv papers get priority injection if they don't survive the scoring on their own.

**Language**: English. The language directive is injected into the Gemini prompt and applies to all posts in the thread. Dutch was removed 2026-05-21 — the random 50/50 mix it had been on served no audience strategy (tech Bluesky is English-dominant even for Belgian devs) and halved reach on a third of posts; the "I'm Belgian" signal already lives in the bio.

**Images**: Mentor and Strategist threads have a 50% chance of attaching an AI-generated illustration via Imagen 4 (`imagen-4.0-generate-001`). Uses the same `GEMINI_API_KEY` — no extra secrets. Images are capped at 976 KB before upload (Bluesky's 1 MB hard limit). Generation uses a two-step pipeline: Gemini first crafts a bespoke visual prompt from the finished thread content, then Imagen 4 generates the image from that prompt. Falls back to a static template if the prompt-crafting call fails. Curator posts use a link card instead (RSS metadata) — image generation is never triggered for Curator mode.

**Voice** is anchored to Frederik Van Hecke's writing style — direct, pragmatic, dry. No hype language. No corporate throat-clearing. Short punchy sentences mixed with longer ones. The system prompts in `src/config.py` include verbatim examples from his writing as style anchors for the model.

**Post formatting**: each post in a thread is generated within the 300-character Bluesky limit. If a post still overflows (rare), it is split at a word boundary — never mid-word. URLs and `#hashtags` are wrapped in Bluesky rich-text facets so they render as clickable links and tags (byte-offset computed against UTF-8 so Dutch accented characters don't break the offsets).

**Images on both platforms**: Mentor and Strategist threads that generate an illustration attach it to both Bluesky (1 MB cap) and Mastodon (8 MB cap). Curator threads use a link card on Bluesky and plain text on Mastodon — generic thumbnails (org logos, default share images) are filtered from the link card rather than cluttering the preview.

**Reliability**: concurrent platform delivery with `asyncio.gather`; if one platform fails the other still posts. Per-thread shared retry budgets for both rate-limit (HTTP 429, honours `Retry-After` / `X-RateLimit-Reset`) and transient errors — a mid-thread failure stops cleanly with the posts already on the wire intact and a `*_partial_delivery` event logged, instead of re-running the whole broadcaster and re-sending earlier posts. State (`seen_articles`, `replied_to`) is stored in a private GitHub Gist and survives across runs — local file fallback kicks in if the Gist is unreachable. Hard cap of 10 mention replies per run. Content generation uses a model priority list (`gemini-2.5-pro` → `gemini-2.5-flash`); API-level failures advance to the next model automatically. 2.5-pro is the primary because at ~4 inference calls per day the cost delta vs. flash is roughly $0.90/month, well worth the higher consistency on a constrained-format JSON task. The 2.5 family runs in thinking-mode by default with the thinking budget counting against `max_output_tokens`; the bot pins `thinking_budget` to each model's minimum (128 for pro, 0 for flash) so the budget goes to content output, not internal reasoning. If the entire chain exhausts on a given run, the broadcast is skipped cleanly (no placeholder posts). Older models (`gemini-1.5-flash-latest`, `gemma-3-27b-it`) remain in the configured list as vestigial fallbacks; they're pruned at startup since Google deprecated them, but if access is re-enabled no code change is needed. At startup the list is pruned to models actually available via the Gemini API (non-fatal: if discovery fails, the configured list is used unchanged). One Bluesky session per run — the session string is cached in the Gist so subsequent runs skip the full credential login; any stale or expired session falls back to a fresh password login transparently. If both the preflight and fallback logins fail (e.g. a transient timeout), Bluesky is skipped for that run rather than crashing.

**Telemetry**: each run writes structured engagement, feed-health, and growth data to three Gist-backed JSON files.

`post_metrics.json` (v4.17) records one row per delivered post (post_id, mode, topic, source domain, pioneer id, had_image / had_link_card, thread position / thread length) and a set of derived formatting features (length_chars, emoji_count, hashtag_count, question_count, time_of_day_bucket) that let later analysis answer "do hashtags correlate with engagement?" without backfilling the schema. The metrics sub-object (likes, reposts, replies, quotes, bookmarks, fetched_at — quotes and bookmarks are Bluesky-only via `quoteCount`/`bookmarkCount`, added v4.19) gets refreshed against each platform's read API once per ~24h — Bluesky `get_posts` batched ≤25 URIs, Mastodon `status` per row. Mastodon 404s on individual rows are treated as orphaned (post deleted upstream) and skipped on future refreshes rather than counted as errors. Rows older than 30 days are pruned. Steady-state ~10–20 metrics-API calls per run; refresh and prune share the same I/O cycle as the broadcast-time recording.

`feed_health.json` (v4.16) tracks each RSS feed's last fetch outcome with a 28-attempt rolling window (~2 weeks at 2 runs/day) so dead or drifting feeds surface rather than via mystery low-volume Curator runs.

`growth.json` (2026-05-08) appends a follower-count snapshot per platform per run via Bluesky's `getProfile` and Mastodon's `account_verify_credentials`. Captures followers, follows, and total posts. This is the success metric for the project's Option 1 commitment (build a following) — per-post engagement counts measure something else.

**Voice diversity**: Mentor and Strategist topic picks track a rolling memory (`recent_mode_topics`, v4.16) so the same topic is not chosen back-to-back. The Mentor pool was expanded from 4 to 12 entries (2026-05-08) and then re-tuned on 2026-05-15: the four original broad anchors (Career, Automation, Work-Life Balance, Learning) were producing soft work-observation variants on the live feed, so they were swapped for four more IT-flavoured observation seeds (code review dynamics across seniority gaps, the half-life of internal documentation, what makes migrations succeed besides "doing them", decisions that look technical but aren't). The pool stays at 12; the 8 originally-specific seeds are untouched. With the 5-slot dedup memory, each topic comes around roughly every 2–3 weeks. The avoidance prompt also includes the 3 most recent post excerpts as concrete "do NOT produce structurally similar text" examples, which catches near-verbatim regenerations that the abstract opener-only signal missed.

**No broken promises (v4.17)**: posts must land complete on their own. The bot has no follow-up mechanism (each run is independent), so teasers like "more soon", "stay tuned", "to be continued", or 🧵 are banned in the prompt and stripped defensively at validation time — including the em-dash fragment shape ("Notes on X — more soon."). Same family as the existing reader-bait-question rule: don't defer substance to a future that does not arrive.

**Curator must produce a take, not a summary** (rewritten 2026-05-21, builds on the 2026-05-08 "lead with the finding" rule). Curator posts follow a three-part structure — HOOK (a specific observation or position, first-person by default), SUBSTANCE (the finding anchored by at least one concrete specific — number, name, mechanism, percentage), then the LINK. Paper-summary phrasings are banned explicitly ("A new paper/study/framework/model [verb]", "Researchers have announced", "The paper argues/shows/claims", and the older "Notes on X" / "Just read X" patterns). Editorial-filler endings are banned too ("Sobering read.", "Worth a read.", "Notable.") — if a draft would end on that kind of suffix, it has not done enough substance. The voice anchor in `src/config.py` defines two registers (strategic-advisory vs casual-narrative) with verbatim samples from askfred.be and frederikvanhecke.com; contractions are register-dependent. Same intent as the no-broken-promises rule: the post is not a bookmark.

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
| `GIST_TOKEN` | Yes | GitHub PAT with `gist` scope — for persistent state storage |
| `GIST_ID` | Yes | ID of the private Gist holding `seen_articles.json` and `replied_to.json` |

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

### Coverage policy

CI keeps a **70% global coverage floor** so broad refactors and non-critical integrations can evolve without making the suite brittle.

For high-risk paths that directly control ranking/generation and post/reply orchestration, CI also enforces **stricter module-level coverage gates (80%)** for:

- `src/utils.py`
- `src/agents.py`
- `main.py` (posting/reply pipeline stages)

On pull requests, if any of those critical modules are changed, at least one focused test file must also be updated (for example `tests/test_utils*`, `tests/test_agents*`, or `tests/test_main*`). This keeps critical behavior protected as implementation details change.

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
- **`LANGUAGE_OPTIONS`** — list of languages the model picks from per run (default `["English"]`; Dutch removed 2026-05-21 — no audience benefit at the cost of halved reach)
- **`IMAGEN_MODEL`** — Imagen model for post images (default `imagen-3.0-generate-002`)
- **`IMAGE_GENERATION_PROBABILITY`** — probability of generating an image for Mentor/Strategist runs (default `0.5`)
- **`MENTION_SANITIZE_MAX_CHARS`** / **`FEED_SUMMARY_MAX_CHARS`** — character caps for mention input and RSS feed summaries (both default 500)
- **`GENERIC_IMAGE_PATTERNS`** — substring list used to skip useless link-card thumbnails (org logos, default share images)
- **`MOMENTUM_PRODUCTS`** / **`MOMENTUM_PRODUCT_BONUS`** — flagship 2026 model names that earn a +4.0 scoring bonus; edited quarterly
- **`GEMINI_MODEL_PRIORITY`** — ordered list of models to try; first API-level failure advances to the next. Gemma models automatically receive inlined prompts (Gemma rejects the `system_instruction` API parameter). At startup, the list is silently filtered to models the API actually reports as available — if discovery fails, the configured list is used unchanged
- **`CONSENSUS_SYNERGY_BONUS`** — score bonus per additional feed that covers the same story (default 1.5)

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
│   ├── bluesky_session.py     # Session string caching via Gist (skip full login on warm runs)
│   ├── broadcasters.py        # Bluesky and Mastodon posting
│   ├── config.py              # Constants, personas, feeds, scoring config
│   ├── facets.py              # Bluesky rich-text facets (clickable URLs + hashtags)
│   ├── file_lock.py           # Cross-platform file locking
│   ├── logger.py              # SafeLogger — strips credentials from output
│   ├── settings.py            # Environment variable loading and validation
│   └── utils.py               # RSS fetching, scoring, metadata scraping, state I/O
├── tests/                     # pytest suite (158 tests)
├── .github/
│   ├── workflows/daily_post.yml      # workflow_dispatch target; triggered by cron-job.org at 08:00 and 14:30 UTC
│   ├── workflows/schedule-health.yml # Read-only daily monitor for missed external dispatches
│   ├── workflows/lockfile-check.yml  # Fails if requirements.txt is stale
│   ├── workflows/codeql.yml          # Weekly CodeQL security + quality scan (Python)
│   └── dependabot.yml                # Dependency vulnerability scanning
├── pytest.ini
├── requirements.in            # Human-edited dependency constraints
└── requirements.txt           # Fully resolved lockfile
```

---

## License

MIT. Built by [Frederik Van Hecke](https://askfred.be).
