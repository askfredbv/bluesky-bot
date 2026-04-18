# Plan v4.14.0 — Voice & Editorial Overhaul

**Status**: Draft
**Goal (user's words)**: *"Share nice stuff, cool things, share positive things that people appreciate. And as a result increase followers and reactions to the posts."*

---

## Editorial shift in one paragraph

Today the bot writes like a newsletter editor: a digest summary, a "which means" gloss, a reader-bait question, three hashtags. That's publication mode. Frederik writes like an enthusiast: first-person reaction, one concrete subject, image often centred, no question, maybe one tag. The shift is from **digest → share**, from **third-person summary → first-person reaction**, from **thread → single post by default**. Structure stays. Personas stay. Pipeline stays. Only the editorial output changes.

Three post archetypes after this change:

| Archetype | When | Shape |
|---|---|---|
| **Share** | Curator (news worth pointing at) | 1 post, lead with the thing, one-line take, link |
| **Take** | Strategist (something to chew on) | 1–2 posts, observation first, no Q at end |
| **Appreciation** | Mentor (something noticed in work/life) | 1 post, image-centred, soft observation |
| **Shoulders of giants** | Any mode (pioneer story, historical "huh" moment) | 1 post, the concrete artifact or anecdote, quiet admiration |

Threads of 3+ posts become rare and need a real reason (e.g. a multi-part story that genuinely doesn't fit in 300 chars).

### A fourth dimension: "On this day" / shoulders of giants

You're a fan of stories like Grace Hopper handing out 30 cm pieces of wire — the distance light travels in a nanosecond — to make latency visceral. Not news, not advice, not reaction to today. *"This person did something clever decades ago and it still makes me stop."* The shape is always the same: name the person, name the concrete artifact (the wire, the punch card, the bug taped in the logbook), one sentence on why it landed. No moral lesson tacked on. No "and that's why we should…". The admiration is the post.

Your original instinct was an "On this day" bot. That's the strongest version of this idea, because **the date does work the prompt can't**. "On this day in 1947, the team running the Mark II found a moth in relay 70 and taped it into the logbook" is a real hook. "Here's a Grace Hopper story" is content. Same anecdote, different gravity.

**The quality bar**: the post has to make *you* go "huh, I didn't know that". Apollo 11 fails. Hopper's moth fails (everyone has heard it). The bar is the obscure-but-true detail — the thing a working IT person could plausibly not know.

Examples that clear the bar:
- The original "shell" was named *shell* because it wrapped the Unix kernel — and Stephen Bourne wrote it specifically to be a programming language, not just a command interpreter
- Karen Spärck Jones invented IDF in 1972 — every search engine on Earth still uses her formula, and she was largely uncredited until the 2000s
- Radia Perlman wrote the spanning-tree protocol *as a poem* in the original spec
- The first webcam (Cambridge, 1991) existed to check whether the coffee pot was empty
- `ping` was named after sonar — Mike Muuss wrote it in one evening in 1983
- The `grep` name comes from the ed editor command `g/re/p` (global / regex / print)
- Margaret Hamilton's team coined the term "software engineering" partly to be taken seriously by the hardware engineers at MIT

Examples that **fail** the bar (skip these):
- Grace Hopper's moth (universally known)
- Apollo 11 landing (universally known)
- Dennis Ritchie died (well-covered)
- Ada Lovelace wrote the first program (textbook material)

**Implementation**: two pools, both must clear the "huh" bar.

```python
# Date-anchored: fires when today's date matches. Framed as "On this day in YYYY..."
PIONEER_EVENTS_DATED = [
    {"month": 12, "day": 21, "year": 1991, "title": "Cambridge coffee pot webcam",
     "detail": "First webcam in history, pointed at a coffee pot so researchers wouldn't walk to an empty one."},
    # ...
]

# Undated: pulled at random when no date match. Framed as the detail itself, no anniversary framing.
PIONEER_FACTS_UNDATED = [
    {"title": "grep is named after an ed command",
     "detail": "g/re/p — global, regex, print. The whole tool is just that one ed command extracted."},
    {"title": "Spärck Jones invented IDF in 1972",
     "detail": "Every search engine still uses her formula. She was largely uncredited until the 2000s."},
    # ...
]
```

**Selection logic** (in Mentor/Strategist topic pick):

1. Today's date matches a `PIONEER_EVENTS_DATED` entry → use it. Frame as "On this day in YYYY…".
2. No date match, but a probability roll (`PIONEER_FALLBACK_PROBABILITY`, default ~0.20 → ~3 posts/week across the two daily runs) hits → pick from `PIONEER_FACTS_UNDATED`, skip ones used in the last 30 days (track in Gist state alongside `seen_articles`). Frame as the detail directly, *no* "did you know" or "fun fact" opener.
3. Otherwise → normal flow.

This gets you ~3 pioneer posts per week on average, with date-anchored ones ("On this day…") sprinkled on top as bonuses on the days they hit.

**Curation discipline (still applies)**: every entry in either pool has to make *you* pause when you read it. The undated pool is the more dangerous one — easier to drift into "pioneer of the week" feel. Two guardrails:
- 30-day cooldown so the same fact doesn't recur quickly
- No framing words like "did you know", "fun fact", "TIL" — just lead with the thing

**Voice for both**: the detail is the post. No moral, no "and that's why we should…", no link to the present. Same restraint as the rest of the v4.14 voice rules.

### 1. Kill reader-bait questions
**Why**: ~80% of bot posts end in a question; ~0% of Frederik's do. Single biggest authenticity gap.
**Where**: `src/config.py` — all three SYSTEM_INSTRUCTIONS prompts.
**How**: Add an explicit ban with examples to refuse:
```
NEVER end a post with a question to the reader. Banned patterns include:
"What do you think?", "How do you handle X?", "Where do you stand?",
"What's your take?", "Have you tried X?", "What about you?",
"Curious to hear...", "Let me know...", "Drop a comment...".
A post ends on a statement, an observation, or a link. Not a question.
```

### 2. Cap hashtags at 2, prefer 0
**Why**: hashtag stacks at the end of a post pattern-match to marketing bots. Bluesky's algorithm doesn't reward stacking. But up to two genuine topic anchors are fine — `#python` on a Python post, `#linux` on a kernel post.
**Where**: `src/config.py` — prompts; defensive trim in `src/agents.py`.
**How**: Prompt instruction:
```
Default to zero hashtags. Maximum two. A hashtag must either:
  (a) replace a noun inline ("teaching #Python to my niece"), or
  (b) anchor the post to a topic feed someone might actually browse
      (#linux, #python, #wetteren) — never a generic mood tag
      (#tech, #innovation, #thoughts, #ai).
If two hashtags both meet the bar, fine. If one is forced, drop it.
```
Defensive trim: if more than 2 hashtags appear in the model's output, keep the first two, drop the rest. Hard cap as safety net, not policy.

### 3. Ban manufactured-warmth words
**Why**: "amazing", "fantastic", "huge", "incredible", "game-changing" — Frederik never uses these. Curator already restricts some; extend to Mentor + Strategist.
**Where**: `src/config.py` — add a shared `BANNED_HYPE_WORDS` list, inline into all three prompts.
**How**:
```python
BANNED_HYPE_WORDS = [
    "amazing", "fantastic", "incredible", "huge", "massive",
    "game-changing", "revolutionary", "mind-blowing", "stunning",
    "groundbreaking", "epic", "insane", "next-level",
]
```
Prompt line: *"Do not use hype words. Banned: {', '.join(BANNED_HYPE_WORDS)}. If a sentence relies on one, rewrite the sentence."*

### 4. Default 1 post for Curator
**Why**: Frederik's best posts are single. Threads imply discussion; he doesn't start discussions.
**Where**: `src/config.py` — Curator prompt; `src/agents.py` — content generation guard.
**How**: Curator prompt now says *"Default to 1 post. A second post only if the story genuinely needs context the link won't carry. 3+ posts is rare and needs a real multi-part reason."* Mentor: 1 post default, 2 max. Strategist: 1–2 posts.

### 5. Rewrite news prompt for first-person reaction
**Why**: Third-person summary mode is what makes arxiv (and everything else) feel "not me".
**Where**: `src/config.py` — `SYSTEM_INSTRUCTIONS_CURATOR`.
**How**: Replace the "summarise the development" framing with:
```
You're sharing this because you find it interesting, not because you're
reporting on it. Lead with the concrete thing (the model, the result,
the change). Follow with a short reaction or implication — what you
noticed, not what readers should think. First-person is allowed
("Spent the morning reading this", "Caught this in passing"). Avoid
third-person newsletter voice ("Researchers have announced...",
"A new study shows..."). The link does the heavy lifting; you frame it.
```

### 6. Fix the 300-char cut-off
**Why**: Posts 5, 9, 10, 11 in recent runs ended mid-word despite the per-post limit.
**Where**: `src/agents.py` — response repair path that splits overflowing posts.
**Investigation**: Check whether the splitter is counting characters vs. UTF-8 bytes vs. graphemes. Bluesky's 300 limit is graphemes (per AT Protocol), but our facets logic uses UTF-8 byte offsets. Likely cause: model returns a 305-char post, splitter trims to 300 chars without word-boundary fallback when the boundary doesn't exist in the last N chars.
**Fix**: Defensive truncation that always backs up to the last whitespace boundary if it exists in the last 40 chars; otherwise hard-break at 297 chars + "…".

### 7. Kill day-of-week openers
**Why**: "Tool Tuesday", "Failure Friday", "Sunday Reset" feel like a content calendar from a corporate marketing playbook.
**Where**: `src/config.py` — Mentor prompt.
**How**: Explicit ban: *"Do not open with day-of-week labels (Tool Tuesday, Failure Friday, Sunday Reset, Motivation Monday). Open with the observation directly."*

### 8. Source mix update
**Why**: Move arxiv + research blogs out of forced injection (they're background, not headlines). Add operator/practitioner sources whose posts feel closer to "share-worthy".
**Where**: `src/config.py` — `RSS_FEEDS`, `SOURCE_TIERS`, `HIDDEN_GEM_SOURCES`.
**How**:
- **Remove from HIDDEN_GEM_SOURCES** (still in feeds, just no longer force-injected): `arxiv.org`, `bair.berkeley.edu`, `ai.stanford.edu`, `vkrakovna.wordpress.com`
- **Add feeds**: Pragmatic Engineer, One Useful Thing (Ethan Mollick), Benedict Evans, Latent Space, AI Snake Oil, Marcus on AI
- **Optional scope broadening**: add a NASA/JPL or space-exploration feed (Mars rovers, JWST). Flips an earlier decision; user explicitly mentioned this is in-character.

---

## Image policy revision

Today: Mentor/Strategist 50% chance of generated image; Curator gets a link card.

After: Curator and Strategist also eligible for generated image when the post is a "share" archetype with no strong link card (e.g. an arxiv finding visualised). Threshold: if the linked page's link-card image is in `GENERIC_IMAGE_PATTERNS`, fall through to image generation. Mentor unchanged.

---

## Files to change

| File | Change |
|---|---|
| `src/config.py` | New `BANNED_HYPE_WORDS`, `BANNED_QUESTION_PATTERNS`, `BANNED_OPENERS` lists. New `PIONEER_STORIES` pool + `PIONEER_PROBABILITY`. Rewrite all three SYSTEM_INSTRUCTIONS. Update `RSS_FEEDS`, `SOURCE_TIERS`, `HIDDEN_GEM_SOURCES`. Reduce default post counts. |
| `src/agents.py` | Pioneer-story branch in Mentor/Strategist topic selection. |
| `src/agents.py` | Defensive post-processing: strip extra hashtags, fix 300-char splitter, optionally enforce single-post default. |
| `tests/test_agents_voice.py` (new) | Unit tests: a synthetic model response with banned words / hashtags / questions gets repaired. |
| `tests/test_post_splitting.py` (extend) | Add cases for 305-char input, no-whitespace-in-last-40-chars input, emoji at boundary. |
| `README.md` + wiki `Content-Modes.md` | Reflect editorial shift. |
| `main.py` | Version bump to v4.14.0. |

---

## Validation

No automated metric will tell us this worked. Two checks:

1. **After 1 week of runs**: read the last 14 posts. Count: questions at end, hashtag count per post, hype-word occurrences, day-of-week openers. Targets: 0 / ≤1 avg / 0 / 0.
2. **After 2 weeks**: eyeball follower count and per-post reactions vs. the 2 weeks before. Matches user's stated success metric.

If after 2 weeks the posts still don't feel right, the issue is the model, not the prompt — escalate to switching the default model or adding stronger few-shot examples from Frederik's actual posts.

---

## Implementation order

1. `src/config.py` — banned-word lists + prompt rewrites + source mix. (Highest leverage, lowest risk.)
2. `src/agents.py` — defensive repair (hashtag cap, 300-char splitter fix).
3. Tests for the repair path.
4. README + wiki.
5. Version bump in `main.py`.
6. Tag v4.14.0, ship, watch one run, iterate.

---

## Out of scope (deliberately)

- Reply behaviour to mentions (separate concern; current 10/run cap is fine)
- New persona ("Hobbyist"? "Operator"?) — premature; retune the existing three first
- Switching away from Gemini 2.5 Flash — see "Validation" escalation step
- Per-post idempotency for thread retries (still a known limitation, separate task)
