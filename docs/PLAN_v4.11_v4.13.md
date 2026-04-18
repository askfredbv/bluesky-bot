# Plan: v4.11.0 → v4.13.0

Remaining adaptations inspired by the BluBot comparison (April 2026).
Slice A (v4.10.0) already shipped: Bluesky facets, Mastodon image parity,
link-card logo filter.

**Hard constraint**: thread architecture stays. No single-post conversion.

---

## Slice B — Content quality → `v4.11.0`

### B1. Momentum products list

**File**: `src/config.py`

Add after `CONSENSUS_SYNERGY_BONUS`:

```python
# Flagship 2026 products — boosted because a post about gpt-5 is categorically
# more consequential than "a new feature in some tool". Edited quarterly.
MOMENTUM_PRODUCTS: List[str] = [
    "gpt-5", "gpt 5", "claude 4", "claude opus 4", "claude sonnet 4",
    "llama 4", "gemini 3", "gemma 4", "o3", "o4",
    "grok 3", "grok 4", "deepseek v4", "mistral large 3",
]
MOMENTUM_PRODUCT_BONUS: float = 4.0
```

**File**: `src/utils.py` — `calculate_relevance_score`, after the product-keyword block:

```python
if any(p in text for p in MOMENTUM_PRODUCTS):
    score += MOMENTUM_PRODUCT_BONUS
```

Remember to add `MOMENTUM_PRODUCTS, MOMENTUM_PRODUCT_BONUS` to the config import in `utils.py`.

**Tests** (extend `tests/test_ranking.py`): item mentioning `"claude 4"` scores `MOMENTUM_PRODUCT_BONUS` higher than otherwise-identical item without it.

---

### B2. Sage Designer — two-step image prompt generation

**File**: `src/agents.py`

Replace the body of `generate_post_image`:

```python
async def generate_post_image(
    api_key: str, topic: str, thread_posts: Optional[List[str]] = None
) -> Optional[bytes]:
    """Generate a visual for a thread via Imagen 3, using a two-step pipeline:
    first craft a bespoke visual prompt from the thread content, then generate.
    Falls back to the static template if the prompt-crafting call fails.
    """
    summary = " ".join(thread_posts or [])[:800]
    visual_prompt = await _craft_visual_prompt(api_key, topic, summary)
    if not visual_prompt:
        visual_prompt = (
            f"A clean, minimal editorial illustration representing the concept: '{topic}'. "
            "No text, no people, flat design style, muted modern palette."
        )
    try:
        return await asyncio.to_thread(_sync_generate_image, api_key, visual_prompt)
    except Exception as exc:
        SafeLogger.warn(
            "image_generation_failed",
            "Imagen 3 image generation failed; posting without image",
            error_type=type(exc).__name__,
            topic=topic,
        )
        return None


async def _craft_visual_prompt(api_key: str, topic: str, summary: str) -> Optional[str]:
    """Use Gemini to craft a bespoke Imagen prompt from the thread content."""
    instruction = (
        "You produce image generation prompts for editorial illustrations. "
        "Output ONE sentence, under 60 words. No text, no people, no hands. "
        "Flat modern design, muted palette. Describe concrete visual elements "
        "(shapes, objects, composition) — not abstract concepts."
    )
    task = f"TOPIC: {topic}\n\nTHREAD SUMMARY: {summary}\n\nOutput the prompt only, no preamble."
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_sync_generate_text, api_key, instruction, task),
            timeout=10.0,
        )
        text = (result or "").strip()
        return text if text else None
    except Exception as exc:
        SafeLogger.info(
            "visual_prompt_craft_failed",
            "Falling back to static Imagen prompt",
            error_type=type(exc).__name__,
        )
        return None
```

You'll need a `_sync_generate_text(api_key, instruction, task)` helper that uses `gemini-2.5-flash` (fastest) with `system_instruction=instruction` and returns `result.text`. Structure mirrors the existing `_sync_generate_image`.

**File**: `main.py` — reorder `broadcasting_stage` so `generate_content` runs first, then the image call receives `thread_posts=content_list`:

```python
content_list, chosen_topic = await generate_content(...)

image_bytes = None
if mode != "curator" and random.random() < IMAGE_GENERATION_PROBABILITY:
    image_bytes = await generate_post_image(
        creds.gemini_api_key, chosen_topic, thread_posts=content_list
    )
```

**Tests** (extend `tests/test_agents.py`):
- mock `_craft_visual_prompt` returning `"glowing orb"` → assert `_sync_generate_image` called with `"glowing orb"`
- mock `_craft_visual_prompt` raising → assert static fallback prompt used

---

### B3. Gemma prompt adaptation

**Problem**: Gemma rejects `system_instruction`. If the chain falls through to `gemma-3-27b-it`, the style prompt is silently dropped → generic corporate output.

**File**: `src/agents.py` — add helper above `generate_content`:

```python
def _is_gemma(model_name: str) -> bool:
    return "gemma" in model_name.lower()


def _build_generate_kwargs(model_name: str, system_instr: str, task: str) -> Dict[str, Any]:
    """Gemma doesn't accept system_instruction — inline it into the user turn."""
    if _is_gemma(model_name):
        return {"contents": f"{system_instr}\n\n---\n\n{task}"}
    return {"contents": task, "config": {"system_instruction": system_instr}}
```

Find every `client.models.generate_content(...)` call in `agents.py` (content + mention-reply paths) and replace with:

```python
client.models.generate_content(model=m, **_build_generate_kwargs(m, instr, task))
```

**Tests** (`tests/test_agents.py`):
- `_build_generate_kwargs("gemma-3-27b-it", "SYS", "TASK")` → `{"contents": "SYS\n\n---\n\nTASK"}` (no `config` key)
- `_build_generate_kwargs("gemini-2.5-flash", "SYS", "TASK")` → `{"contents": "TASK", "config": {"system_instruction": "SYS"}}`

---

## Slice C — Resilience & hygiene → `v4.12.0`

### C1. Bluesky session string caching

**New file**: `src/bluesky_session.py`

```python
"""Bluesky session persistence — cache the session string in the Gist state
store to avoid a fresh login every run. Reduces 429 surface and startup time."""

from typing import Any, Optional
from atproto import AsyncClient
from src.logger import SafeLogger


async def load_or_login(
    client: AsyncClient, username: str, password: str, state_store: Any
) -> None:
    """Try the cached session first; fall back to full login on any failure.

    state_store must expose .get(key) -> Optional[str] and .set(key, value).
    """
    cached: Optional[str] = None
    try:
        cached = state_store.get("bluesky_session")
    except Exception:
        cached = None

    if cached:
        try:
            await client.login(session_string=cached)
            SafeLogger.info("bluesky_session_reused", "Reused cached Bluesky session")
            return
        except Exception as e:
            SafeLogger.info(
                "bluesky_session_stale",
                "Cached session invalid; performing fresh login",
                error_type=type(e).__name__,
            )

    await client.login(username, password)
    try:
        state_store.set("bluesky_session", client.export_session_string())
    except Exception as e:
        SafeLogger.warn(
            "bluesky_session_save_failed",
            "Failed to persist Bluesky session to state store",
            error_type=type(e).__name__,
        )
```

**Storage**: piggyback on the existing Gist — same file structure, new key `bluesky_session`. Confirmed with user: same Gist, not a separate one.

**Wire-up**: replace both `bsky_client.login(creds.bluesky_username, creds.bluesky_password)` calls in `main.py` (content_prep_stage + broadcasting_stage fallback) with `await load_or_login(bsky_client, creds.bluesky_username, creds.bluesky_password, state_store=gist_state_store)`.

**Security audit**: before merging, verify `SafeLogger` redaction won't ever log the session string. Add a regex pattern for JWT-shaped tokens if needed: `r'[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'`.

**Tests** (`tests/test_bluesky_session.py`):
- cached session login succeeds → one `login` call, no `export_session_string` (session not rewritten)
- cached session login raises → fresh login runs, new session saved
- no cached session → fresh login, new session saved
- state_store.get raises → fresh login (no crash)

---

### C2. Gemini model self-discovery

**File**: `src/agents.py` or `src/utils.py` (whichever hosts the model iteration)

```python
async def filter_available_models(api_key: str, priority: List[str]) -> List[str]:
    """Query Gemini for available models, remove any from priority that aren't
    listed. Only removes — never adds — so experimental models don't sneak in."""
    try:
        client = genai.Client(api_key=api_key)
        models_list = await asyncio.to_thread(client.models.list)
        available = {m.name.split("/")[-1] for m in models_list}
        filtered = [m for m in priority if m in available]
        if filtered != priority:
            SafeLogger.info(
                "model_priority_adjusted",
                "Adjusted priority based on available models",
                original=priority, adjusted=filtered,
            )
        return filtered or priority
    except Exception as e:
        SafeLogger.warn(
            "model_discovery_failed",
            "Using configured priority unchanged",
            error_type=type(e).__name__,
        )
        return priority
```

Call once at startup (in `main.py::main()` or first stage) and pass the filtered list down to the content-generation path.

**Tests**: mock `client.models.list()` to omit one entry → verify filtered; mock to raise → returns original.

---

### C3. CodeQL workflow

**New file**: `.github/workflows/codeql.yml`

```yaml
name: CodeQL

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: "0 6 * * 1"  # Monday 06:00 UTC

jobs:
  analyze:
    name: Analyze Python
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v4
      - uses: github/codeql-action/init@v3
        with:
          languages: python
      - uses: github/codeql-action/analyze@v3
```

Add CodeQL badge to README under the project title.

---

## Slice D — Feed breadth → `v4.13.0`

### D1. Hidden gem expansion

**Validation step (run locally, DO NOT commit the script)**:

```python
# scripts/validate_new_feeds.py  -- throwaway
import feedparser, httpx

CANDIDATES = [
    "https://thegradient.pub/rss/",
    "https://magazine.sebastianraschka.com/feed",
    "https://bair.berkeley.edu/blog/feed.xml",
    "https://ai.stanford.edu/blog/feed.xml",
    "https://ai.meta.com/blog/rss/",
    "https://www.microsoft.com/en-us/research/feed/",
    "https://txt.cohere.com/rss/",
    "https://vkrakovna.wordpress.com/feed/",
]

for url in CANDIDATES:
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        parsed = feedparser.parse(r.text)
        n = len(parsed.entries)
        status = "OK" if n > 0 and not parsed.bozo else "BROKEN"
        print(f"{status:8} {n:4} items  {url}  (HTTP {r.status_code})")
    except Exception as e:
        print(f"ERROR    ----  {url}  ({type(e).__name__})")
```

Only commit URLs that return `OK` with item count > 0. In the PR description, list the dropped candidates and why (e.g. "ai.meta.com/blog/rss/ → HTTP 404, skipped").

**File**: `src/config.py` — for each OK feed, append to `RSS_FEEDS`, add a `SOURCE_TIERS` entry, and add academic/research ones to `HIDDEN_GEM_SOURCES`.

Candidate tier values (tune to taste):
- `thegradient.pub`: 8
- `magazine.sebastianraschka.com`: 8
- `bair.berkeley.edu`: 9
- `ai.stanford.edu`: 9
- `ai.meta.com`: 9
- `microsoft.com`: 7
- `txt.cohere.com`: 7
- `vkrakovna.wordpress.com`: 7

Academic/research-leaning → add to `HIDDEN_GEM_SOURCES`: `thegradient.pub`, `bair.berkeley.edu`, `ai.stanford.edu`, `vkrakovna.wordpress.com`.

**Calibration**: more feeds = more multi-source stories. After the first 3 runs, check whether `CONSENSUS_SYNERGY_BONUS` should drop from 1.5 to 1.2 to prevent one viral story dominating every Curator run. Manual eyeball decision, no test automation.

**No tests** — feed list is data, not logic.

---

## Ship order and release names

| Version | Slice | LOC (est) | New tests | Risk |
|---|---|---|---|---|
| v4.11.0 | B — Content quality | ~300 | ~6 | low (fallback paths exist) |
| v4.12.0 | C — Resilience | ~250 | ~6 | medium (credential handling) |
| v4.13.0 | D — Feed breadth | ~20 | 0 | low, but observe output shift |

Per-release: bump version in `README.md` + `main.py` (the `run_started` log line) + wiki `Home.md`, update test count, `git tag v4.X.0`, `gh release create` with heredoc notes.

---

## Known deferred items (intentionally out of scope)

- **Threads (Meta) cross-posting** — deferred pending user decision on Meta association.
- **Weekend rest logic** — user wants engagement data first before committing.
- **NVIDIA SD3 image gen** — Imagen 3 works, duplicate provider not worth the secret surface.
- **BluBot dialect rotation** — would dilute the askfred voice anchor (the deliberate differentiator).
- **Per-post thread idempotency** — noted in the v4.9.1 plan; still a separate larger task when/if retry-mid-thread duplicates become a real problem.

---

## Reuse notes

To resume this plan in a fresh Claude session: `@docs/PLAN_v4.11_v4.13.md  continue with Slice B`. The plan is self-contained — no dependence on the original conversation.
