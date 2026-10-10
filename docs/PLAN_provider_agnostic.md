# Plan: Provider-agnostic AI platform — pick a flavour, and a failover

> **Status — 2026-10-10. Planning only. Nothing here is built and nothing is scheduled.**
> Agreed with Frederik on 2026-10-10 as a BHAG: the bot should let you choose which AI
> platform writes it, plus a failover, and should *tell you what that choice costs you*.
> The only thing shipped so far is the narrow first step it grew out of: a single
> Anthropic model appended below the Gemini chain (#180, `d1661b2`).
>
> Read this before building any of it. Several of the obvious designs are wrong for
> reasons that only became visible by measuring, and the measurements are in
> [Evidence base](#evidence-base--2026-10-10) at the bottom.

---

## The goal

Today the model chain is `GEMINI_MODEL_PRIORITY` plus one Anthropic entry at the end.
Switching the bot to a different AI platform means editing code in three files and
knowing which of them matters.

The BHAG: **state a preferred platform and a failover, and have the bot resolve that
into a working configuration and report what you gave up.** Two lines of configuration,
full scope — text *and* images.

The second half of that sentence is the actual feature. A grid you fill in by hand is
not useful; a grid the bot derives and explains to you is.

---

## Reconciliation with the operating goal

`AGENTS.md` sets the operating goal: *run reliably, cost no recurring human time, never
embarrass its author*, and says to prefer changes with no recurring human cost. A
provider-agnostic layer is, on its face, in tension with that: more surface, more
provider APIs to track, and a voice trial every time a model changes.

This plan is only worth executing if that tension is resolved rather than ignored.
Three ways it is:

1. **Most of the bot is not voice-critical.** Breaking the LLM calls into capabilities
   (below) shows that exactly one of them — `text.post` — produces prose a human reads.
   The others emit JSON arrays, keep/drop verdicts, and an image prompt. Changing the
   platform for those needs no voice trial at all. The recurring cost applies to one
   cell of the matrix, not the whole thing.
2. **The provider facts must be machine-checked, not documented.** A table of provider
   quirks maintained by hand *is* recurring human cost, and it rots silently. Executable
   checks turn it into CI cost instead. See [The facts table](#the-facts-table).
3. **It reduces one existing cost.** Every Google deprecation so far (imagen-3,
   imagen-4, 2.0-flash, 1.5-flash, gemma, and `thinking_budget` on 3.8) has cost an
   afternoon. A second platform that is already wired and already probed turns some of
   those afternoons into a config change.

If a stage below cannot be justified against that goal, it does not get built.

---

## Two axes, kept apart

The single most important structural decision, and the one most likely to be got wrong:

**Channel and provider are orthogonal.**

| Axis | Owns | Examples |
|---|---|---|
| **Channel** | How long, what shape, which platform mechanics | Bluesky (300 chars), Mastodon (500) |
| **Provider** | Which model writes it, and how you have to ask | Gemini, Anthropic, OpenAI |

A provider adapter must never contain the word "Bluesky". Its contract is *"produce 1–3
strings of at most M characters matching this schema"*, where **M is handed down by the
channel layer as the minimum across active channels**.

Keep them separate and adding a channel and adding a provider are independent pieces of
work. Conflate them and it becomes an N×M problem that nobody will maintain.

### A latent assumption to fix on the way past

`src/agents.py` imports `MAX_POST_LENGTH_BSKY` and treats it as *the* generation limit —
both in the prompt instruction and in `_validate_thread_shape`. That is correct only
because Bluesky happens to be the strictest active channel. It is not computed as a
minimum over channels, so adding a shorter-limit platform, or dropping Bluesky, would
silently generate to the wrong target.

Mastodon's extra 200 characters are **headroom for mechanics, not for more content**:
`ensure_mastodon_source_link`, `apply_mastodon_tags` and `number_mastodon_thread` all
default to `MAX_POST_LENGTH_MASTODON` and append *after* validation. That is the
`AGENTS.md` §7 split working as intended and must survive any refactor here.

---

## Capabilities

Not "text" and "images". The bot makes six distinct kinds of call, and they have
different requirements:

| Capability | What it does | Voice-critical | Channel-parameterised | Notes |
|---|---|---|---|---|
| `text.post` | The daily Curator/Mentor/Strategist thread | **Yes** | **Yes** (M chars) | JSON contract, thinking off, ~1500 output tokens |
| `text.classify` | Propose Mastodon discovery tags | No | No | Small JSON array |
| `text.review` | KEEP/DROP each proposed tag | No | No | **Must differ from `text.classify`** |
| `text.prompt` | Draft the visual prompt | No | No | Text in, text out |
| `image.generate` | The post visual | No | No | Gemini and OpenAI only |
| `text.reply` | Mentions and Phase 4b replies | Yes | Yes | Phase 4b parked; mentions live |

Two things fall out of this table that are not obvious before writing it:

- **Four of six capabilities are plumbing.** Nobody reads their prose. Switching their
  provider is cheap and needs no trial. This is what makes the BHAG affordable.
- **`text.review` must use a different model from `text.classify`.** `src/config.py` is
  explicit that no distinct reviewer means no tags. Today that is satisfied within one
  provider (3.7-flash proposes, 3.5-flash reviews) and the same file admits both passes
  sharing a provider means their mistakes can correlate. A cross-provider reviewer would
  be **strictly better**, and is a free side effect of this work.

---

## The facts table

A project-owned table of what each provider can do. The user never edits it.

Per provider and capability: whether it is supported, the model id, the price, and the
**quirk** — most importantly how reasoning is turned off, because the bot's 1500-token
output cap makes that load-bearing rather than cosmetic.

```
anthropic  text.*          claude-haiku-5-5   $0.10/$0.50   thinking={"type":"disabled"}
anthropic  image.generate  —                   —            UNSUPPORTED
openai     text.*          gpt-6-luna         $0.10/$0.50   reasoning={"effort":"none"}
openai     image.generate  gpt-image-2        per-image     —
gemini     text.*          gemini-3.7-flash   free tier     thinking_config={"thinking_budget":0}
gemini     image.generate  gemini-3.1-flash-image  —        generate_content, not generate_images
```

### Why this table must be executable

**It is a claim about the world that will silently become false.** On 2026-10-10 two of
its rows were wrong within an hour of being written, and only an API call corrected them:

- Gemini 3.8 Flash *accepts* `thinking_budget` and reports zero reasoning tokens,
  although the release notes say the parameter is replaced by `thinking_level`.
- `gpt-6-luna` accepts `reasoning.effort: "none"` — the floor is lower than the docs
  implied.

So the facts table is not documentation. It is a fixture with a test: a generalised
probe walks every row and asserts the model id still resolves, reasoning still turns
off, the structured output still validates, and the price still matches. That is
`scripts/probe_fallback.py` grown up, and it is the thing that keeps this plan from
decaying into stale assertions.

---

## The resolver, and the output that is the actual feature

You configure two things:

```
LLM_PRIMARY=anthropic
LLM_FAILOVER=openai
```

The resolver walks the capability list, resolves each against the facts table, enforces
the invariants, and **prints the result**. At startup, and behind an `--explain` flag:

```
primary=anthropic  failover=openai

capability      resolved chain                    note
text.post       claude-haiku-5-5 → gpt-6-luna     voice-critical; trial PASSED 2026-10-10
text.classify   claude-haiku-5-5 → gpt-6-luna
text.review     gpt-6-luna → claude-haiku-5-5     distinct-from-classify: satisfied
text.prompt     claude-haiku-5-5 → gpt-6-luna
image.generate  gpt-image-2                       ! anthropic cannot generate images
                                                  ! if openai is down, posts ship text-only
est. cost ≈ $0.40/month at 8 calls/day
```

That printout is the deliverable. "Understand what your selection entails" is not
achieved by documentation; it is achieved by the bot telling you, in the terms of your
own configuration, every time it starts.

### Invariants the resolver enforces

1. `text.review` resolves to a different model from `text.classify`. Within one provider
   if it has two suitable models; across providers otherwise.
2. The channel constraint M is the **minimum over active channels**, not a hardcoded
   platform constant.
3. Every resolved chain is non-empty, or the gap is reported at its severity.

### Severity, because degradation is not binary

| Severity | Meaning | Example |
|---|---|---|
| `BLOCKS` | The bot cannot run | No provider resolves for `text.post` |
| `DEGRADES` | Runs, output is worse | No `image.generate`: posts ship text-only |
| `NONE` | Fully satisfied | — |

Losing images is `DEGRADES`, and the matrix must say so in those words rather than
leaving the reader to work it out.

---

## Stages

Each stage is useful on its own and none requires the next. Effort figures are rough.

### Stage 0 — Native structured output for the Curator contract (~2h)

Switch `text.post` from *"please return JSON"* in the prompt to schema-enforced
structured output. All three platforms support it.

**Worth doing even if the rest of this plan is never built.** `gpt-6-luna` ignored the
prompt-only contract 3 times in 8; a server-enforced schema would likely have made it
viable, and it removes a whole class of per-provider variance before any adapter exists.

*Gate:* the Curator's valid-output rate does not drop on `gemini-3.7-flash`, measured
over at least 8 generations against the real prompt.

### Stage 1 — Capability list in config (~2h, pure refactor)

Introduce the six capabilities and route today's calls through them. Current behaviour
is the default. No new providers, no behaviour change, no new config surface.

Also fixes the `MAX_POST_LENGTH_BSKY`-as-generation-limit assumption by computing M as
the minimum over active channels.

*Gate:* 998 tests still green, and a production run is byte-for-byte unremarkable.

### Stage 2 — Provider adapters behind one interface (~4h)

One adapter per platform, each owning its own reasoning-off quirk and structured-output
shape. Plus a **conformance suite** every adapter must pass: turns reasoning off,
honours the output cap, returns schema-valid JSON, surfaces errors as retryable or not.

*Gate:* all three adapters pass conformance against the live APIs, not mocks.

### Stage 3 — The resolver and the explain output (~3h)

`LLM_PRIMARY` / `LLM_FAILOVER`, the facts table, the invariants, the severity model, and
the printout above.

*Gate:* three configurations resolve correctly, including one with a `DEGRADES` gap and
one with a `BLOCKS` gap.

### Stage 4 — Generalised probe (~2h)

`probe_fallback.py` extended to walk every row of the facts table against the live APIs
and fail loudly on drift. Manual dispatch, same pattern as the existing probes.

*Gate:* the probe catches a deliberately corrupted facts row.

---

## Non-goals

- **No runtime provider switching.** The choice is configuration, read at startup.
- **No cost cap enforced by the bot.** It reports the estimate; the human reacts.
  A bot that silently stops posting to stay under budget is worse than a bill.
- **No per-capability configuration surface.** See [Open decisions](#open-decisions).
- **No voice trial automation.** Tempting, and out of scope: `em_dash_detected` and
  `hype_words_detected` measure rule compliance, not whether the prose is any good.
- **No new providers beyond the three the user holds accounts for.**
- **This does not get the bot off Google.** Only OpenAI covers both text and images, so
  a Google-free configuration is possible but has a single point of failure for images.

---

## Evidence base — 2026-10-10

Five candidates run against the **real** Curator prompt (imported from `src/config.py`,
not a paraphrase), 8 samples each, scored by the repo's own validators:

| model | valid | mean latency | failure mode |
|---|---|---|---|
| `gemini-3.8-flash` | 8/8 | 2.94s | — |
| `gemini-3.7-flash` (incumbent) | 7/8 | 3.22s | 1× over 300 chars |
| `claude-haiku-5-5` | 7/8 | **1.64s** | 1× over 300 chars |
| `gemini-3.6-flash` | 6/8 | 1.90s | 2× over 300 chars; fenced its JSON 7/8 |
| `gpt-6-luna` | **4/8** | 3.28s | **3× ignored the JSON contract** |

With reasoning genuinely off, no model reported reasoning tokens. At
`thinking_level: "low"` — which is *not* off — 3.6 and 3.7 burned 1,300–1,400 reasoning
tokens against the 1,500 cap and truncated their JSON mid-string.

**Voice did not separate the candidates.** Two rounds of blind picks over four articles:
round 1 favoured `claude-haiku-5-5` (3 of 4 picks), round 2 reversed and favoured
`gpt-6-luna` on the half the reader had a clear read on. Eight picks cannot separate five
models. **Do not cite round 1 as evidence for a primary swap** — that is exactly the
over-read the second round was run to catch.

Harness kept at `C:\claude\bluesky-bot\scratch\voice_trial_2026-10-10\`.

---

## Open decisions

Neither blocks Stage 0 or Stage 1.

1. **Configuration surface.** Two lines (`LLM_PRIMARY` / `LLM_FAILOVER`) with everything
   derived, or per-capability preference? *Recommendation: two lines.* Per-capability is
   more powerful and much easier to misconfigure, and the derived matrix already gives
   the visibility that per-capability control would be reached for.
2. **Behaviour on a `BLOCKS` gap.** Refuse to start, or start degraded and shout?
   *Recommendation: refuse at config time.* A silently disarmed capability is the exact
   failure mode `probe_fallback.py` exists to prevent, and a release that cannot post is
   better discovered on the laptop than at 07:00 UTC.

---

## Related

- `AGENTS.md` — the operating goal and review priorities this plan must satisfy.
- `docs/BACKLOG.md` — item 4 is the next actual work; this plan is not queued ahead of it.
- `docs/RETRO_2026-05-08.md` — why this project distrusts plans that were never measured.
- `src/llm.py` and `scripts/probe_fallback.py` — the narrow first step, already shipped.
