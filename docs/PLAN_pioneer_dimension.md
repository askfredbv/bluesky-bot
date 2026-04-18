# Plan — Pioneer / "On this day" dimension

**Status**: Draft
**Relationship to v4.14**: independent of the voice overhaul. Can ship before, alongside, or after. Suggested: ship *after* v4.14 lands so we measure pioneer impact against a stable baseline voice.
**One-line goal**: make the bot share obscure-but-true tech-history details and forgotten-but-wonderful projects a few times a week, with extra weight when today's date earns it, while staying inside the v4.14 voice rules.

**Scope clarification**: not just pioneers. Four flavours of entry, all judged by the same "huh, I didn't know that / I'd forgotten that" bar:

1. **Pioneer facts** — Spärck Jones / IDF, grep etymology, Perlman's spanning-tree poem
2. **Forgotten artifacts** — Cambridge coffee pot webcam, original `finger` daemon
3. **Wonderful weird projects** — The Register's PARIS (Paper Aircraft Released Into Space, ~2010), Stanford bunny, Carmack's Quake source release, Story of Mel
4. **Forgotten heroes** — people who made a real dent but aren't household names. Andy Bechtolsheim (wrote the original $100k cheque to "Google Inc." before Google existed), Anders Hejlsberg (Turbo Pascal → Delphi → C# → TypeScript, one career), Bram Moolenaar (vim, single maintainer for 30 years). Distinct from pioneer-facts: pioneer = "did this clever thing"; forgotten hero = "*this person* deserves more credit than they get"

The PARIS series is the canonical example for category 3: amateur cleverness, beautifully documented, ran as a live multi-part adventure. The bot pointing at it years later isn't news — it's *"this happened, it was great, it shouldn't be forgotten"*. Same energy as a pioneer fact, different content type.

---

## What we're adding

Two curated pools in `src/config.py`:

```python
PIONEER_EVENTS_DATED = [
    {"month": 12, "day": 21, "year": 1991,
     "title": "Cambridge coffee pot webcam",
     "detail": "First webcam in history. Pointed at a coffee pot so researchers wouldn't walk to an empty one."},
    # 30-50 entries; each must clear the "huh" bar
]

PIONEER_FACTS_UNDATED = [
    {"id": "grep-etymology", "category": "pioneer",
     "title": "grep is named after an ed command",
     "detail": "g/re/p — global, regex, print. The whole tool is just that one ed command extracted."},
    {"id": "sparck-jones-idf", "category": "pioneer",
     "title": "Spärck Jones invented IDF in 1972",
     "detail": "Every search engine still uses her formula. She was largely uncredited until the 2000s."},
    {"id": "bechtolsheim-google-cheque", "category": "hero",
     "title": "Andy Bechtolsheim wrote a cheque to a company that didn't exist",
     "detail": "September 1998. $100,000 to 'Google Inc.' before incorporation. Page and Brin had to register the company so they could deposit it. Bechtolsheim had also co-founded Sun a decade earlier."},
    {"id": "paris-paper-plane", "category": "project",
     "title": "The Register flew a paper plane to the edge of space",
     "detail": "PARIS — Paper Aircraft Released Into Space. 2010, weather balloon, multi-part live build series. Recovered 100km from launch.",
     "link": "https://www.theregister.com/Tag/Paris/Paper%20Aircraft%20Released%20Into%20Space/"},
    # 30-50 entries; same bar across all three categories (pioneer / artifact / project)
]

PIONEER_FALLBACK_PROBABILITY = 0.20   # ~3 posts/week across two daily runs
PIONEER_COOLDOWN_DAYS = 30            # don't repeat the same fact within this window
```

Each entry has a stable `id` (or month/day for dated) so the cooldown tracker can reference it.

---

## Selection logic

In Mentor/Strategist topic selection, before the normal `SECONDARY_TOPICS` pick:

1. **Date match**: today's `(month, day)` matches an entry in `PIONEER_EVENTS_DATED` and that entry's `id` is not in the cooldown set → use it. Frame: *"On this day in YYYY, …"*
2. **Undated fallback**: no date match, but `random.random() < PIONEER_FALLBACK_PROBABILITY` → pick a `PIONEER_FACTS_UNDATED` entry not in the cooldown set. Frame: lead with the detail directly. No "did you know" / "fun fact" / "TIL".
3. **Otherwise**: normal `SECONDARY_TOPICS` flow, untouched.

Curator (morning, news mode) is **not** affected. Pioneer dimension only attaches to Mentor/Strategist (afternoon).

---

## State tracking

Extend Gist state (`seen_articles.json` or a sibling key):

```json
{
  "links": [...],
  "recent_topics": [...],
  "pioneer_recent": [
    {"id": "grep-etymology", "posted_at": "2026-04-15T15:30:00Z"},
    {"id": "sparck-jones-idf", "posted_at": "2026-04-09T15:30:00Z"}
  ]
}
```

On each pioneer post: append `{id, posted_at}`, prune entries older than `PIONEER_COOLDOWN_DAYS`. Selection logic filters out any `id` still in the cooldown set.

---

## Voice rules (inherits from v4.14)

- The detail is the post. Single post.
- No moral, no "and that's why we should…", no link-to-present
- No "did you know" / "fun fact" / "TIL" openers
- No reader-bait question at the end (same v4.14 ban)
- 0–1 hashtag (same v4.14 cap)
- Image generation **on by default** for pioneer posts — the artifact (the wire, the coffee pot, the punch card) carries the post

Two prompt templates needed (one for dated, one for undated). Both injected into the existing Mentor/Strategist persona — they don't get a new persona, just a new content path.

---

## The quality bar (non-negotiable)

Every entry, in either pool, must make *you* pause when you read it. Concrete tests:

- ✅ "grep is named after `g/re/p`" — most working devs don't know this
- ✅ "Cambridge coffee pot was the first webcam" — niche but verifiable
- ❌ "Grace Hopper's moth = first bug" — universally known
- ❌ "Apollo 11 landed July 1969" — universally known
- ❌ "Ada Lovelace wrote the first program" — textbook material
- ✅ "PARIS — paper plane to the edge of space" — niche, joyful, beautifully documented, fits the "shouldn't be forgotten" bucket
- ❌ "SpaceX landed a booster" — current, well-covered, not in the spirit

If the candidate fact is in a pop-history listicle, it doesn't go in. Curation is one person's gut (yours) — not crowdsourced, not LLM-generated. The list grows organically as you encounter things.

---

## Files to change

| File | Change |
|---|---|
| `src/config.py` | Add `PIONEER_EVENTS_DATED`, `PIONEER_FACTS_UNDATED`, `PIONEER_FALLBACK_PROBABILITY`, `PIONEER_COOLDOWN_DAYS`. Two prompt templates. |
| `src/agents.py` | New `select_pioneer_topic(seen_data) -> Optional[dict]` helper. Wire into Mentor/Strategist topic selection before `SECONDARY_TOPICS` pick. |
| `src/utils.py` | Extend state read/write to include `pioneer_recent`. Prune-on-write. |
| `main.py` | Persistence stage updates `pioneer_recent` after a pioneer post. |
| `tests/test_pioneer_selection.py` (new) | Date-match wins; cooldown filters; probability gate; undated entry not picked when cooldown blocks all candidates; date-match still works even when probability roll fails. |
| `tests/test_state.py` (extend) | `pioneer_recent` round-trips through Gist; old entries pruned. |
| Wiki `Content-Modes.md` | Document the new dimension. |

---

## Implementation order

1. Seed the two lists with **at least 20 entries each** in `src/config.py`. This is the prerequisite — without enough entries, the cooldown will starve the selector. (If you want, I can generate a starter list of 40 candidates and you keep/cull.)
2. State extension (`pioneer_recent` in Gist).
3. Selection helper + wire-in.
4. Prompt templates.
5. Tests.
6. Ship behind a feature flag (`PIONEER_DIMENSION_ENABLED = True`) so you can yank it cleanly if it lands wrong.

---

## Success measurement

This is the harder half of the question. Pioneer posts are different from news posts — the goal isn't engagement-per-post (those will likely get *fewer* reactions than hot AI takes), it's **whether the bot's overall feed feels more like Frederik**. Three measurement layers:

### Layer 1: Cadence sanity check (week 1)

After 7 days of running, confirm the mechanics work:
- Did pioneer posts fire on the expected days?
- Did `pioneer_recent` cooldown actually prevent repeats?
- Did any post end up framed wrong (corny opener, moral tacked on)?

This is a build/QA check, not a success metric. Pass = green-light to keep it on.

### Layer 2: Engagement deltas (weeks 2–6)

Compare pioneer posts vs. non-pioneer posts over a 4-week window. Track per post:
- Likes
- Reposts
- Replies (separate signal — replies on pioneer posts are *especially* interesting because pioneer posts don't bait questions)
- New follower correlation (hard to attribute precisely; eyeball weekly delta)

**Hypothesis to confirm or reject**:
- Pioneer posts get fewer likes per post than news posts (expected — narrower audience)
- Pioneer posts get higher *engagement quality* — replies that share related facts, reposts from people who appreciate the niche
- Total weekly follower growth is flat or up vs. the 4 weeks before pioneer launch

If pioneer posts get *zero* engagement consistently, the quality bar isn't being hit — culling needed.
If they get strong engagement, increase `PIONEER_FALLBACK_PROBABILITY` from 0.20 to 0.30.

### Layer 3: The "does it feel like me" check (ongoing)

The metric that actually matches your stated goal. Once a month, scroll the last 30 posts and ask:
- Would I have posted this myself?
- Does the feed feel like one coherent voice, or two stitched together?
- Are the pioneer posts the ones I'm proud of, or the ones I scroll past?

This is subjective, ungameable, and the only metric that actually answers the original question. Numbers from Layer 2 inform it; they don't replace it.

### What to log

Add structured log events so Layer 1 + 2 don't require manual scraping:

```python
SafeLogger.info("pioneer_post_selected", "Pioneer dimension fired",
                pool="dated" or "undated",
                entry_id="grep-etymology",
                days_since_last_pioneer=N)
```

Then a quick `gh run list --workflow=daily_post.yml` filter can pull pioneer-firing runs for the engagement comparison.

---

## What success is *not*

- Not "more posts per week" (the bot already posts enough)
- Not "more followers in week 1" (no signal will be visible that fast)
- Not "every pioneer post goes viral" (impossible by design — the bar makes them niche)
- Not "Layer 2 numbers go up forever" — pioneer posts are a *flavour*, not a growth lever

---

## Risk and rollback

**Main risk**: corny pioneer posts that read like LinkedIn "today in tech history" filler. Mitigations:
- Quality bar (curated, not generated)
- Voice rules ban the corny openers
- 30-day cooldown so even good entries don't burn out
- Feature flag for clean rollback

If after 6 weeks the pioneer posts feel forced or aren't moving Layer 3 in the right direction, flip the flag off. The lists stay in config — you can revisit the format later (longer threads? image-only posts? once-a-week digest?) without re-implementing the data.

---

## Open questions for you

1. **Scope**: Mentor + Strategist only, or also Curator on quiet news days (low-signal mornings)?
2. **Language**: pioneer entries are written once in `config.py` — English only, or both languages? (Suggestion: write the `detail` in English, let the Gemini prompt translate when the run language is Dutch.)
3. **Want me to draft 40 candidate entries** as a starting list for you to curate down to 20–30 keepers?
