# Plan: Proactive replies

## Why this exists

The bot currently only broadcasts. It doesn't participate. Social accounts grow by *participating* — replying with substance to other people's posts. That's where reach and trust compound.

The risk: one tone-deaf reply to the wrong person burns more trust than a month of good replies builds. So this plan is sequenced to avoid that: **recon first, then a minimum-viable reply loop with human approval, then expansion if it's working.**

Three phases. Only start Phase 2 once Phase 1 has produced a ranked, evidence-based watchlist. Only start Phase 3 once Phase 2 has ~2 weeks of approved-reply data showing replies landing.

---

## Phase 1 — Virtual follow (recon, no reply code yet)

Goal: turn the candidate list from gut-feel into a ranked watchlist backed by actual post samples.

**What to build:**

- `scripts/audit_watchlist.py` — one-shot script (not part of the daily pipeline). Takes a list of handles from a file, fetches the last ~10 public posts from each across Bluesky and Mastodon, writes a per-handle report.

**Per handle, the report captures:**

| Field | How measured |
|---|---|
| Topic fit | Does the handle post about things the bot covers? Tag each of the 10 posts against TOPIC_MAP + SECONDARY_TOPICS + pioneer categories. Score = % of posts in-scope. |
| Voice compatibility | Does the handle's register match the askfred voice (first-person, dry, specific)? Rough heuristic: presence/absence of BANNED_HYPE_WORDS, BANNED_QUESTION_PATTERNS, average post length, first-person usage. Flag handles that sound LinkedIn-y or promotional. |
| Reply opportunity rate | % of posts that are statements/observations (reply-able) vs reposts/screenshots/bare links (not reply-able). |
| Posting cadence | Posts per week averaged over the sample. Below ~3/week and the feed is too quiet to be worth watching. |
| Engagement substrate | Average `replies + reposts` per post. High-likes/low-replies accounts broadcast more than they converse — harder to break into usefully. |

**Output:** a markdown table ranked by a composite score, plus a sample-post appendix so a human can eyeball whether the scores match reality.

**Candidate seed list (starter — add/remove freely):**

*Bluesky*
- `simonwillison.net` — practical LLM tooling, matches voice
- `jeremyhoward.bsky.social` — fast.ai, practitioner
- `emilymbender.bsky.social` — AI skepticism, nuance required
- `swyx.io` — AI engineering
- `jbhuang.bsky.social` — ML explainers
- `karpathy.bsky.social` — verify active first
- `pytorch.bsky.social` — release posts only
- `huggingface.bsky.social` — release posts only
- Dutch/Belgian tech journalists — needs manual discovery before script runs

*Mastodon*
- `@simon@simonwillison.net` — same person, cross-posts
- `@glyph@mastodon.social` — Python, dry voice
- `@inthehands@hachyderm.io` — Paul Cantrell, programming + academia
- `@b0rk@jvns.ca` — Julia Evans, if on Mastodon
- `@jwildeboer@social.wildeboer.net` — European open-source
- `@mcc@mastodon.social` — graphics/systems
- Belgian `mastodon.be` tech folks — manual discovery

**Explicitly excluded:**
- Frontier-lab CEOs (Altman/Pichai/Amodei) — replies sound like ambulance-chasing
- "AI news" aggregator accounts — amplifies the noise the bot is trying to be opposite of
- Political accounts of any stripe — voice neutrality is non-negotiable
- Hot-take / hype accounts

**Success criterion for Phase 1:** a ranked table where the top 5 handles are defensibly good matches (scores align with human reading of the sample posts), and you can point to 2–3 you'd stake a first reply on. Effort: ~1h to write the script, ~30min to review the output.

---

## Phase 2 — Minimum viable reply loop (2–3 handles, human approval)

Only start once Phase 1 has a ranked watchlist.

**Scope:**
- Watchlist: top 2–3 handles from Phase 1, hard-coded in `src/config.py` as `PROACTIVE_REPLY_WATCHLIST`
- Cadence: once per day, scan watchlist, pick **at most one** reply candidate
- **Human approval gate: the bot does not post the reply. It writes the draft reply to the Gist (new file: `pending_replies.json`) along with the parent post context. You approve or reject via a manual action.**

**Files to change:**

- `src/config.py` — add `PROACTIVE_REPLY_WATCHLIST` and a `PROACTIVE_REPLY_PROMPT` system instruction. The prompt's hardest rule: reply must add information or perspective, not just react. No "great point," no "thanks for sharing," no hype.
- `src/agents.py` — new `generate_proactive_reply(parent_post_text, parent_author_handle, author_recent_context)`. Uses same Gemini fallback chain. Returns draft text or None (bot is allowed to decide "nothing worth saying here" — that option has to be explicit in the prompt or it'll reply to everything).
- `src/proactive.py` — **new module**: `scan_watchlist(clients)`, `pick_reply_candidate(posts)`, `stage_draft_reply(parent, draft)`. Candidate picker filters posts from the last 12h with any replies or reposts (dead posts waste effort), skips quote-posts and pure-link posts, skips anything matching BANNED_QUESTION_PATTERNS in parent (don't chase reader-bait).
- `.github/workflows/proactive_scan.yml` — new workflow, daily ~10:00 UTC. Runs scan + stage. Does not post.
- Separate manual workflow `.github/workflows/approve_pending_reply.yml` — `workflow_dispatch`, reads `pending_replies.json`, posts the first entry or marks it rejected based on input.
- Tests.

**Reply generation prompt — the hard rules:**
- First person, matching the askfred voice anchors in STYLE_GUIDELINES
- Must add information the parent post doesn't already have (an adjacent fact, a counter-example from experience, a link)
- Under 200 characters — replies should feel conversational, not like another broadcast
- Return a literal `"SKIP"` sentinel if the post doesn't warrant a substantive reply. **This option is essential.** Most posts shouldn't get a reply.
- Never agree-and-amplify without content. Never end on a reader-bait question.

**State shape (`pending_replies.json`):**
```
{
  "pending": [
    {
      "id": "uuid",
      "platform": "bluesky",
      "parent_post_uri": "at://...",
      "parent_author": "simonwillison.net",
      "parent_text": "...",
      "draft_reply": "...",
      "generated_at": "...",
      "expires_at": "+24h — too stale to post after that"
    }
  ],
  "posted": [...],     // kept for 14d for traction analysis
  "rejected": [...]    // kept for 14d to spot prompt drift
}
```

**Success criterion for Phase 2:** over 2 weeks, 10–14 drafts staged, you approve ≥50% of them, and the approved replies generate ≥1 meaningful back-and-forth reply from the parent author or a third party. If approval rate is <30%, the reply prompt needs tuning before expansion.

---

## Phase 3 — Expansion and (maybe) approval removal

Only start once Phase 2 has 2+ weeks of good data.

- Expand watchlist to top 8–10 handles
- If approved:rejected ratio is consistently >80% and no reply has required damage control, *consider* removing the approval gate for handles you've built trust with — but keep it for any new handle added later
- Feed traction data back into Phase 1 ranking: handles where replies got engagement get weighted up; handles where replies got ignored get weighted down or dropped

**Non-goal:** never go fully unattended across the whole watchlist. The cost of one bad reply to someone like Timnit Gebru or a journalist is too high to automate away.

---

## Non-goals (plan-wide)

- **Chasing viral posts** — replying to hot threads for visibility is the ambulance-chaser playbook. Skip.
- **Template replies / macros** — every reply is generated fresh against the parent post. No "here's my go-to response about X."
- **Replies on the bot's own threads to simulate engagement** — obvious and embarrassing if caught.
- **Following accounts as a growth tactic** — the bot doesn't follow anyone. Reading handles in Phase 1 is via the public API, not via the bot's social graph.

---

## Rollback

- Phase 1 is a one-shot script — nothing to roll back.
- Phase 2 adds a new module, a new workflow, and a new state file. Revert the commits; delete the state file from the Gist. Zero production impact since the bot never posts unattended in this phase.
- Phase 3 — if unattended replies go wrong, set the approval gate back on by flipping a config flag. Don't delete the watchlist — the handles are still useful under the gate.
