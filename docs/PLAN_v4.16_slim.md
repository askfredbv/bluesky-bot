# Plan: v4.16 — Slim down before the next feature

**Status**: Draft
**Goal**: address the three real-fat issues (utils.py junk drawer, config.py mixing concerns, dataclass-payload tax) before adding any new content dimension. Pure refactor — no behaviour change, no version bump beyond a minor.

**Why now, not later**: each is 1–2 features away from genuinely getting in the way. The cheapest moment to do a refactor is when the test suite is at 146 green and there's no in-flight feature work — i.e., today.

**Non-goals**: rewriting working code for taste, adding new abstractions, splitting `agents.py` (it's growing but still under threshold).

---

## Current state (v4.15.0)

| File | LOC | Concerns it actually owns |
|---|---|---|
| `src/utils.py` | 923 | state I/O, RSS fetch, scoring, URL safety, retry, image compression, JSON repair, Gist sync, file locks |
| `src/config.py` | 525 | constants, prompt templates, curated data (39 pioneer entries, 25 feeds, persona variants, banned-word lists) |
| `src/agents.py` | 690 | LLM call, voice trim, persona selection, mention sanitisation, image gen, validation, pioneer selection |
| `main.py` | 309 | pipeline orchestration with 3 frozen-dataclass payloads |

Test count: 146 passing. Test files are **already organised by concern** (`test_ranking.py`, `test_retry.py`, `test_utils_state_io.py`, `test_utils_url_safety.py`, etc.) — the split below mostly aligns source layout with the existing test layout, which is a strong signal the boundaries are real.

---

## Slice 1 — Split `utils.py` into focused modules

**Why first**: biggest pain, biggest payoff, mechanical.

### Target layout

| New file | Contents (move from utils.py) |
|---|---|
| `src/state_io.py` | `load_seen_articles`, `save_seen_articles`, `load_replied_to`, `save_replied_to`, `update_seen_articles`, `update_replied_to`, `_load_gist_state`, `_save_gist_state`, `_load_state_from_store`, `_save_state_to_store`, `_atomic_write_json`, `_load_json_with_repair`, `_ensure_pioneer_field`, `prune_pioneer_recent`, `_file_lock` |
| `src/feeds.py` | `fetch_news`, `fetch_single_feed`, RSS-specific helpers |
| `src/scoring.py` | `calculate_relevance_score`, source-tier helpers, momentum logic |
| `src/url_safety.py` | SSRF guards: IP validation, DNS pinning, domain allow/block checks, `normalise_url`, `get_link_metadata` |
| `src/retry.py` | `retry_with_backoff` decorator |
| `src/image_io.py` | Pillow compression / MIME detection |

`src/utils.py` deleted. (Or kept as a thin re-export shim for one release, then deleted in v4.17 — see "Migration approach" below.)

### Migration approach

Two options, pick one:

**(a) Atomic rewrite** — single PR, all imports updated everywhere, `utils.py` deleted. Simpler, one commit, but one big diff to review.

**(b) Shim release** — new files created, `utils.py` re-exports for backward compat (`from src.state_io import *`, etc.), call sites migrated file-by-file across two PRs. Safer if there were external consumers; *we have none*, so the shim is overhead with no benefit.

**Pick (a).** Solo project, no downstream.

### Files affected (imports to update)

Quick grep map — each line is one file that needs its `from src.utils import ...` line rewritten:

- `main.py`
- `src/agents.py`
- `src/broadcasters.py`
- `tests/test_*.py` (most test files import directly from utils — their imports change but their assertions don't)

Running the test suite catches any miss.

### Risks

- **Circular imports.** `state_io.py` imports `prune_pioneer_recent` which uses `PIONEER_COOLDOWN_DAYS`; `agents.py` imports both `state_io` and pioneer config. Should be fine — `config.py` has no reverse dependencies.
- **Test file naming drift.** Existing `test_utils_state_io.py` and `test_utils_url_safety.py` reference the old name. Rename to `test_state_io.py` and `test_url_safety.py` for consistency, OR leave the test file names and just update their imports. (Suggestion: leave names — renaming tests is churn for no reader benefit.)

### Verification

`pytest` — 146 passing before, 146 passing after. Zero behavioural diff.

### Effort estimate

~2 hours focused. The actual splitting is mechanical; most time goes to running the test suite after each move and fixing the import errors it surfaces.

---

## Slice 2 — Split `config.py`

**Why second**: smaller code change, but unlocks faster prompt iteration (you can edit voice rules without scrolling past 39 pioneer entries).

### Target layout

| New file | Contents (move from config.py) |
|---|---|
| `src/config.py` | Typed knobs only: `MAX_POST_LENGTH_BSKY`, `RECENT_POSTS_LIMIT`, `PIONEER_FALLBACK_PROBABILITY`, all the timeouts, `BANNED_HYPE_WORDS`, etc. — anything that's a tunable constant. |
| `src/prompts.py` | All the writing: `STYLE_GUIDELINES`, `SYSTEM_INSTRUCTIONS_MENTOR`, `SYSTEM_INSTRUCTIONS_CURATOR`, `MENTOR_PERSONA_VARIANTS`, `CURATOR_PERSONA_VARIANTS`, `PIONEER_PROMPT_DATED`, `PIONEER_PROMPT_UNDATED` |
| `src/data/pioneer.py` | `PIONEER_EVENTS_DATED`, `PIONEER_FACTS_UNDATED` (the 39 entries) |
| `src/data/feeds.py` | `RSS_FEEDS`, `HIDDEN_GEM_SOURCES`, `SOURCE_TIERS`, `MOMENTUM_PRODUCTS`, `GENERIC_IMAGE_PATTERNS` |
| `src/data/topics.py` | `SECONDARY_TOPICS`, `TOPIC_MAP`, `PRODUCT_KEYWORDS`, `GROUNDBREAKING_KEYWORDS` |

`src/data/__init__.py` exists but stays empty (no re-exports — explicit imports are clearer).

### Why three "data" files instead of one

The pioneer file alone is 190 lines. Co-locating with feeds and topics would push the combined file past 400 lines of just data — same junk-drawer problem we just fixed in `utils.py`. Splitting by *what kind of data* keeps each file readable on one screen.

### Why a `data/` subfolder vs flat

Flat keeps imports shorter (`from src.feeds_data import ...` vs `from src.data.feeds import ...`). But the subfolder communicates "this is curated content, not behaviour" — meaningful for readers (and for your future self deciding whether a change needs a code review or just a content review).

**Pick subfolder.** The naming clarity beats the import-length cost.

### Imports affected

Every file that currently does `from src.config import (...)` needs its import block sorted into the right new module. Mechanical. Test suite catches misses.

### Banned-word lists — config or data?

`BANNED_HYPE_WORDS`, `BANNED_QUESTION_PATTERNS`, `BANNED_OPENERS` are arguably "data" but they're tightly coupled to the voice trim logic in `agents.py`. Keep them in `config.py` (they're enforcement rules, not curated content).

### Risks

- **Circular import: `prompts.py` doesn't reference data, but `agents.py` imports both.** Fine.
- **Wiki Configuration page** lists every config knob — needs an update mentioning the new layout. Low priority; the page is for users, who only care about env vars.

### Effort estimate

~1 hour. Mostly cut/paste with import shuffling.

---

## Slice 3 — Replace 3-stage frozen dataclass chain with a single mutable `RunContext`

**Why third**: only worth doing if we expect 1–2 more cross-stage features. Pure ergonomics — no production-behaviour change.

### The current pain

Adding `pioneer_entry` in v4.15 required edits to:
- `ContentPrepPayload` (added field)
- `BroadcastPayload` (added field + plumbing)
- `AutomationPayload` (added field + plumbing)
- 3 dataclass constructors, 3 reads, 1 write

That's the tax. With one cross-stage field per feature, this scales to 4 edits/feature. Not catastrophic, but it's pattern-bloat: the architecture asks you to write the same field name in five places before you can read it once.

### Target

```python
@dataclass
class RunContext:
    # Set in mode_selection_stage
    mode: str
    current_hour_utc: int

    # Set in content_prep_stage
    seen_data: Dict[str, Any] = field(default_factory=dict)
    news_items: List[Dict[str, Any]] = field(default_factory=list)
    link_meta: Optional[Dict[str, Any]] = None
    bsky_client: Any = None
    recent_posts: List[str] = field(default_factory=list)
    pioneer_entry: Optional[Dict[str, Any]] = None

    # Set in broadcasting_stage
    content_list: List[str] = field(default_factory=list)
    chosen_topic: str = ""
    thread_pause_profile: str = ""
    bsky_broadcast_client: Any = None
```

Each stage takes `RunContext` in, mutates it, returns it. Adding a field = one edit (the dataclass) plus the producer/consumer.

### What we lose

- **Frozen-ness.** No more compile-time guarantee that a stage can't accidentally mutate upstream state. In practice this guarantee was costing more than it bought — solo project, every stage already trusts every other stage.
- **Explicit "what does this stage need" signature.** Currently `broadcasting_stage(content_prep: ContentPrepPayload, ...)` documents the dependency. With `RunContext`, every stage takes the same parameter and the dependency is implicit.

That second loss is real. Mitigation: have stages assert on entry (`assert ctx.mode is not None`) for any field they require but don't produce. Cheap, runtime-checked, and it's executable documentation.

### What we gain

- One file change per cross-stage feature instead of three
- Easier debugging — you can dump `RunContext` at any point and see the full run state
- Easier testing — fixtures construct one `RunContext`, not three nested payloads

### Should we do this slice?

**Maybe defer.** The current dataclass chain isn't broken, just a tax. If we expect <2 more cross-stage features in the next 6 months, the refactor isn't worth its cost. If we expect more (likely — the editorial menu keeps growing), do it.

**Recommendation**: do it. Same reason as slices 1+2: cheaper now than later, and the test suite is the strongest it's ever been.

### Effort estimate

~1.5 hours. Smaller code change than slice 1 but more risk — the contract between stages changes, so any test that constructs a payload by hand needs updating. Grep `ContentPrepPayload(` etc. for the hit list.

### Risks

- **`test_main_stages.py` constructs payloads directly** — needs rewrite. Not large, but every test in that file changes.
- **`assert` on required fields** can fire in production. Either use proper exceptions, or trust the orchestrator. (Suggestion: trust the orchestrator, omit the asserts. The test suite catches missing wiring.)

---

## Sequencing and ship plan

| Slice | Effort | Risk | Defer? |
|---|---|---|---|
| 1 — Split utils.py | ~2h | Low | No — do first |
| 2 — Split config.py | ~1h | Low | No — do second |
| 3 — RunContext | ~1.5h | Medium | Optional — do if more features expected |

**Order matters.** Slice 1 shouldn't import from a freshly-split config (slice 2), so doing 1 → 2 keeps each diff focused. Slice 3 is independent of both.

**Single release** (v4.16.0 — refactor only) covering slices 1 and 2 minimum. Slice 3 either rolls into the same release or ships as v4.16.1 a week later, depending on appetite.

**No version bump for behaviour** — this is plumbing. README/wiki get a one-line note ("v4.16: source layout reorganised; no behaviour change") and the architecture wiki page gets updated to reflect the new file map.

---

## Success criteria

After v4.16 lands:

- `pytest` shows 146 passing (zero behavioural change)
- `wc -l src/*.py` shows no file over ~400 lines
- Adding the next cross-stage feature requires editing fewer files than v4.15 did
- A new contributor (or your future self in 6 months) can find "where does the bot decide what to post about" in under 60 seconds by reading the file tree

If any of these don't hold after the refactor, we did it wrong.

---

## What NOT to do in this refactor

- **Don't split `agents.py`**. It's at 690 lines but the concerns are tightly coupled (LLM call + voice trim + persona + pioneer selector all participate in `generate_content`). Splitting now creates artificial boundaries; revisit if it grows past 1000 lines.
- **Don't introduce new abstractions** (interfaces, protocols, base classes). Pure file moves only. New abstractions are how refactors balloon.
- **Don't rename functions** unless their location changes their meaning (e.g. `_load_gist_state` becomes `state_io.load_gist_state` — losing the underscore because module-private isn't the same as file-private). Even then, keep the rename minimal.
- **Don't touch the prompt text** while moving it from `config.py` to `prompts.py`. Cut/paste only. Editorial changes are a separate commit, separate review, separate release.

---

## Rollback

Each slice is one commit. Revert by `git revert <sha>`. No feature flags needed — there's no behaviour to gate.
