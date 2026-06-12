# Voice audit — independent critic via Gemini

A periodic, human-run check on whether the bot's posts still hold Frederik's
voice or drift to generic AI prose. Run with **Gemini** (a different model
family than the one that writes the posts — gemini-3.5-flash), so the critic's
blind spots differ from the author's. This is the content-side equivalent of
`AGENTS.md` (which briefs Codex for code review).

**Why Gemini, not Claude:** independence is the value. A critic from a
different lineage than the writer catches what the writer's blind spots miss.
The 2026-06-12 trial proved it: Gemini calibrated to the same three weakest
posts a Claude read had flagged, AND found a pattern Claude missed — the
template-opening tell ("The most interesting bit in this paper…" repeated
across 4 of 12 posts) — which landed as a real fix in commit `7c4b5f1`.

## When to run

- After a model swap or a Curator/voice prompt change (did it help or drift?).
- Roughly monthly otherwise, or whenever the feed "feels off".
- Not a gate — a periodic sharpening pass.

## Automated version (monthly issue)

`.github/workflows/voice-audit.yml` runs `scripts/run_voice_audit.py` on the
1st of each month (and on manual `workflow_dispatch`). It uses a **Pro-tier
Gemini auditor** (different from the gemini-3.5-flash writer) and opens a
GitHub issue titled "Voice audit — <date>" with the critique. The issue is a
**draft to review** — it leads with "claims to verify, not verdicts", and
nothing is auto-applied. Deliberately monthly, not daily: voice drift is slow,
and the point is a report you actually read, not plat bandwerk. The manual
process below stays available for ad-hoc runs (e.g. right after a prompt
change) — and is a cleaner cross-context check (you running Gemini yourself is
more independent than a Gemini model auditing near-Gemini output).

## How to run

1. Pull the last ~12 posts (public API, no auth):
   ```
   python - <<'PY'
   import httpx
   r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
                 params={"actor":"askfred.be","limit":12,"filter":"posts_no_replies"}, timeout=15)
   for i,it in enumerate(r.json()["feed"],1):
       print(f'{i}. {it["post"]["record"].get("text","")}'); print()
   PY
   ```
2. Paste the prompt below into Gemini, then the numbered posts under it.
3. Read Gemini's audit against the judging criteria (further down).
4. **Verify before acting.** Gemini's findings are claims, not verdicts —
   check each against the live feed and your own read before changing a
   prompt, exactly as Codex's code findings get verified against the code.
   Flag → verify → both must be right.

## The Gemini prompt (paste-ready)

The voice principles below are a condensed mirror of the canonical source
(global `CLAUDE.md` voice anchor + `memory/user_writing_style.md`). Keep them
in sync if the canon changes.

```
You are an independent voice critic for the tech account @askfred.be. The
voice is Frederik Van Hecke's. Judge whether each post below holds the voice
or drifts to generic AI prose. Be specific: quote exact phrases. No general
praise. Report findings plainly — no hype or motivational phrasing in your
own analysis (that is the very thing you are critiquing).

THE VOICE (two registers):
- Register A (strategic-advisory): longer sentences, NO contractions ("it is",
  not "it's"), careful argument, a sharp closing sentence. E.g. "The tool
  rarely fails. Adoption fails." / "That is a rational position for them. It
  is a strategic problem for you."
- Register B (casual-narrative): short sentences, contractions OK, dry
  understatement, concrete brands/prices/numbers. E.g. "That's it. I've had
  it with gravity. Seriously." / "In short: that does not fly."

FINGERPRINT (both): dry understatement, em-dashes for clarification, first-
person presence, concrete specifics (numbers, names, mechanisms), NO hype, NO
motivational-poster, leads with the finding.

WHAT DRIFT LOOKS LIKE (flag it):
- generic-clever phrasing with no concrete anchor
- borrowed clichés ("fails to survive contact with reality", "a different
  game entirely", "the bill is coming due")
- summary-form instead of a stance of one's own
- jargon density that shuts the reader out
- absence of first person where it would be natural
- TEMPLATE OPENINGS repeated across posts ("The most interesting bit in this
  paper…", "The bit that landed for me…") — fine once, an AI tell when every
  external-content post opens the same way

TASK:
1. Per post: one line — does it hold the voice, or where does it slip? Quote it.
2. The 2-3 weakest posts, with exactly why.
3. One thing the account does consistently well, and one recurring weakness.

THE POSTS:
[paste the numbered posts here]
```

## How to judge whether it was worth it

| Worth it if… | Not worth it if… |
|---|---|
| It independently flags the posts you'd flag (calibration) | It praises a post you read as weak and nitpicks a strong one |
| It quotes exact phrases | Generic praise / "consider adding more detail" |
| It finds something you missed (bonus — this is the payoff) | It only restates the obvious |
| Its register distinction is correct | It marks Pioneer history-facts as "too dry" |

If Gemini's read lands near yours AND adds something, it earned the pass — fold
the finding into the prompt (a real example: `7c4b5f1`). If it lands far off,
skip it and rely on Codex (code) + your own eye (voice).

## Note on the critic's own voice

In the trial Gemini's analysis ran a bit hot ("drop-the-mic quality",
"forces authority") — mild irony for a voice critic. Take its **findings**,
not its **phrasing**. The "report plainly, no hype" line in the prompt is there
to curb this; it helps but does not fully fix it.
