# Backlog

Living list of pending work and parked ideas. Bot is shipping at **v4.32.0** (2026-10-29). Release notes for every version live on the [GitHub releases page](https://github.com/askfredbv/bluesky-bot/releases) — this file does not duplicate them.

> **Before working on this project, read [`RETRO_2026-05-08.md`](RETRO_2026-05-08.md).** Six weeks of infrastructure landed on top of a hardcoded fallback that was shipping placeholder posts to production the whole time. The retro names the pattern (metrics-as-substitute-for-reading) and the corrective (open the live feed first). Don't repeat it.
>
> **It has been repeated twice since.** The 2026-10-12 cold read (2 of 20 posts clearing the bar) and the 2026-10-19 image-prompt finding (six months of captioned images, found by opening three files) are the same pattern. Both were found by looking at output and could not have been found any other way. The corrective has been written down three times and automated zero times — see corrective 3 in the 2026-10-19 finding, which is the attempt to fix that.

---

## Priority order

Updated 2026-10-29. Open: items 4 and 6, plus the §3 observational items. Closed
items, findings and older changelog entries moved verbatim to [`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md).

*Anything recorded here that is meant to be built gets a GitHub issue, not a bullet.
A bullet has no state and nothing closes it — which is how the corrective in
`RETRO_2026-05-08` sat unautomated through three occurrences of the same bug.*

4. **De-bias phase 2 — bound the keyword magnitudes.** `PRODUCT` (+5) and `GROUNDBREAKING`
(+7) can total +12 against a tier spread of 7 (3.0 → 10.0), so text signals outvote
provenance. **Deliberately gated on a week of item 1's
`keyword_substring_only_matches` data** — phase 2 is re-tuning magic numbers by hand,
which is what the abandoned landmark gate (§3) teaches against doing without data.

<!-- original numbering kept: closed items 1-3, 5 and 7 are in BACKLOG_ARCHIVE.md -->

6. **Explicit `--mode`.** `main.py` picks the mode from `current_hour < 11`, so a dispatch
delayed past 11:00 UTC would silently turn a Curator run into a Mentor run. Never
observed (44 of 44 runs mapped correctly, 22× 07:00→curator, 22× 14:30→mentor) and it
needs a four-hour delay, so this is cheap insurance, not a live defect. The workflow
already has a `force_mode` input; the cron dispatches just do not pass it.

<!-- original numbering kept: closed items 1-3, 5 and 7 are in BACKLOG_ARCHIVE.md -->

8. **Observational items** — wait for more runs, then decide (see §3)
9. **The plan** — `PLAN_engagement.md` covers everything else (see §4). Phase 2 and 3 stay
data-gated. Phase 4b is code-complete and **parked by decision**, not merely dormant
(see §1).

---

## §1 — Operating goal (since 2026-10-06)

> **SUPERSEDED 2026-10-06.** The goal is now: run reliably, cost no recurring human time,
> never embarrass its author. Growth is deprioritised. The record below is history, not
> current intent.
>
> **Why.** Growth here needs replies, and replies need Phase 4b (§2.1), which is
> human-gated by design. Frederik declined to staff that approval queue, so the bot stays a
> pure broadcaster. Measured that day: Bluesky 26 → 78 followers over five months and 392
> posts; Phase 4b has run once ever (2026-05-21), one draft, rejected; 68 posts in 30 days
> drew 42 interactions, 41 of them zero — too little signal to steer by.
>
> **To reopen:** staff the Phase 4b queue and start at §2.1. Nothing is deleted.
>
> **One exception, 2026-10-26 to 2026-11-24, Frederik's decision.** The afternoon post
> moved from 16:30 to 19:00 Brussels for four weeks (#229) to test whether a US-daytime
> slot raises follower inflow. It was prompted by Shreyas's BluBot: its follower rate
> doubled after its 2026-10-05 schedule change, while at the same weekly volume (14 posts)
> ours stayed flat. The test is allowed under the goal above because it costs nothing
> recurring: same two posts a day, no new runs, no model spend, no code path, and one
> readout command. Pass/fail rule, fixed before the test started: 30 or more new followers
> in 28 days, otherwise revert (#230). This is one test, not a change to the goal:
> `AGENTS.md` still applies to everything else.

The Option 1 history (2026-05-08 to 2026-10-06) is in [`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md).

---

## §2 — Closed issues

Nothing here is open. The summary of what was resolved is in [`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md).

---

## §3 — Observational (wait for data)

- **Cheap-LLM scoring pass — the half of this that is still open.** Most of what this entry described shipped on 2026-10-06: the magnitude-blind −12 topic penalty is now a recency-decayed cooldown below the tier spread, the Curator records the topic it actually posted, time decay is bounded, keyword matching is word-anchored, and the shortlist is source-mixed rather than `ranked[:limit]`. The trigger ("launches still being missed after v4.25.0's feeds have had a few weeks") fired on Gemini 4 Argon, 2026-09-30, and that is what prompted the work.

  **What is left is the instrument question**, and it is parked pending data. A hand-weighted linear sum on an uncalibrated scale is arguably the wrong tool for "which story matters most today": keyword density tracks genre, not importance, so a terse tier-10 launch ("Argon is live.") still loses to a mediocre preprint that happens to say "benchmark". Measured 2026-10-06: 6.50 against 14.75.

  **Do not rebuild an exemption gate.** Two attempts, ~11 review rounds, neither converged: (1) detect "this is a launch" from the headline with a regex — leaked on word order, punctuation, decimal versions, mid-word stems, title/description bleed, short names ("o3" inside "o365"); (2) count distinct publishers covering the same flagship — leaked on headline variance, alias splitting ("GPT-5" vs "GPT 5"), and same-org domains. Both infer importance from text, which is unbounded. A second opinion (Gemini, 2026-09-04) agreed independently, and also shot down moving diversity to a selection-time score margin.

  **The direction, when it is worth doing:** a cheap-LLM pass over the top 15–20 candidates — classify topic, detect genuine launches, assign a 1–10 impact score. Fractions of a cent per run, and it deletes the regex surface rather than patching it. **Validate it in shadow mode first:** log its top 3 beside the heuristic's top 3 for two weeks and compare, wiring nothing to the posting path. Parked 2026-10-06 because the shadow run ends in a decision someone has to read, and that is a recurring human cost (`AGENTS.md`, operating goal).

  **Before any of that, read the data already being collected:** `keyword_substring_only_matches` (how much the old loose matching was paying out) and the de-bias phase 2 question in the priority list.

- **Mastodon discovery tags — three things the probe cannot prove (live 2026-09-09).** Tags ship on every Mastodon post. `Mastodon Tag Probe (diagnostic)` is the standing check; re-run it after any prompt or model change and read BOTH numbers, because retention alone cannot validate a reviewer.

  **1. The voice gate has never fired on real output.** `raw` and `sanitized` have been identical in every probe run — the model has never once proposed a hype word or mood tag — so `#Revolutionary` has only ever been rejected in a unit test. The gate is proven by test, not by traffic. Watch the logs for `mastodon_tags_rejected`: the first one is the gate earning its place.

  **2. Two model passes can still both be wrong.** Cross-model review removes the self-approval path; it does not make semantic errors impossible, and nothing available does. This is accepted residual risk, not an unfinished fix. The blast radius is one wrong tag on one Mastodon post, deletable, and the rollback is `MASTODON_TAGS_ENABLED = False`.

  **3. Tag quality is a brand judgement, and the output is not deterministic.** The same post produced `#OOP` on one run and `#ObjectOrientedProgramming` on another, `#AIObservability` then `#Observability`. Read a week of the live Mastodon feed rather than the probe. If tags read off-topic more than occasionally the fix is the prompt (`agents._MASTODON_TAG_PROMPT`), never a bigger keyword map — that is the same text-inference dead end the landmark gate died on twice.

  **Watch the logs too:** `mastodon_tags_invalid_verdict` means the reviewer stopped returning usable decision sets (a model regression, distinct from a genuine all-DROP), and `mastodon_tags_no_reviewer` means discovery pruned the chain to one model and tagging correctly switched itself off.
- **~~The Register main feed drift~~ — DECIDED 2026-10-06: BOTH FEEDS STAY. Do not re-propose removing them.** The v4.13 removal condition (drop `headlines.atom` if it surfaces content outside the AI/tech sweet spot) did fire — a live pool on 2026-10-06 carried "Only the finest Swedish Bork will do for Stockholm station", "Scientists find planet made on new matter recipe" and "BepiColombo sheds its ride" into the top 12. Frederik overrode it: The Register is on-topic, fast, and the writing is good, and a feed list without it would not be his. **The condition was wrong, not the feed.** Off-topic items reaching the shortlist is a *selection* problem, to be handled by scoring and `select_candidates`, not by cutting a source whose hit rate is high. If the noise ever becomes worth acting on, tune selection.
- **`CONSENSUS_SYNERGY_BONUS` retune.** Currently `1.5` per additional feed. Across 27 feeds, a viral story covered by 5+ sources gets `+6.0` on top of its base score — could start dominating every Curator run. Drop to `1.2` if the Curator starts repeatedly picking the same wire-story everyone covers over genuinely distinctive items.
- **`google-genai` 2.x migration.** Currently pinned at `1.75.0` and shipping fine — `gemini-3.5-flash` is producing sharp content. Latest is `2.2.0`, but 1.x is still receiving releases (`1.75.0` exists alongside `2.2.0`), no Dependabot alerts open against the package, and no 2.x capability is on the immediate roadmap. Migrating now is the exact "infrastructure-over-output" trap the 2026-05-08 retro named. Triggers to revisit: (a) a run fails with a 1.x-specific SDK bug (diagnostic surface will catch it), (b) Dependabot files a CVE, (c) Phase 4b or another planned feature needs a 2.x-only capability. Until one fires, the pin stays.
- **`config.py` split — trigger long since passed.** The `utils.py` half of this item is **done**: #81–#84 split it into `state_store` / `net_safety` / `retry` / `news`, and it is 196 lines now, not the 923 this item was written against. `config.py` went the other way — 525 lines then, **1007 now**. The remaining plan: constants stay, prompt text moves to `prompts.py`, curated data to `src/data/`. Mechanical, ~3h with tests. Not urgent, but stop citing a trigger that already fired.
- **Mentor estimation-topic over-saturation — revisit if it recurs.** A snapshot on 2026-05-21 showed 4 estimation-variant posts in 25, suggesting the seed "estimating your own time vs estimating someone else's" was over-firing. On re-check 2026-05-24, post-rewrite data showed 1 estimation post in 2 Mentor runs — within expected range for a 12-seed pool with 5-slot dedup (`main.py:553`). The earlier saturation likely came from the pre-2026-05-15 pool where broad anchors like "Career" pulled toward estimation under multiple seeds. **Revisit if Mentor shows >25% estimation rate over a 2-week window.** Fix options when triggered: widen dedup window from 5 to 8, or split the estimation seed into two narrower seeds, or drop it temporarily. Don't act prophylactically — let the validators run.
- **Audit script Mastodon path.** `scripts/audit_watchlist.py` returns opaque HTML errors when the Mastodon side fails. **2026-05-05**: added a `account_verify_credentials` preflight that runs once before iterating candidates and surfaces a clear message ("token belongs to @user" on success; "MASTODON_API_BASE_URL is wrong / token under-scoped" on failure with the curl command to verify). Root-cause fix is still in `.env` — the user has `MASTODON_API_BASE_URL=https://mastodon.social/@askfred` (a profile URL) where `https://mastodon.social` (the API base) is needed. Bot's posting still works in Actions because the GitHub secret has the right value; only local audit runs are affected. Revisit when adding new Mastodon-only candidates would benefit from automated scoring.
- **Voice formatting A/B — Track C is moot under the current goal.** Observation 2026-04-27: the feed feels flat, so maybe occasional emojis or a topical hashtag would lift engagement. Track A (instrumentation) and Track B (image reliability) both shipped. **Track C never will in this form:** it was gated on "4+ weeks of engagement data" plus brand approval, and engagement data at this scale is noise (68 posts, 42 interactions, 41 of them zero), so it cannot decide anything. `AGENTS.md` now says not to tune on it.

  What survives is the brand question, which never needed the data: **voice is a brand decision, not a metric decision.** If emojis or question hooks are ever wanted, it is Frederik's call on how the feed should read, made once and applied consistently — not an experiment. A reader returning to a feed that starts using emojis after 137 dry posts notices the inconsistency either way.

  **Not precedent:** v4.26.0 shipped Mastodon discovery tags outside this gate, on a *mechanism* argument (a followed tag is Mastodon's discovery engine, the tags sit at the broadcaster, generation is unchanged). That says nothing about emojis, question hooks, or hashtags on the Bluesky copy.

- **Two follow-ups from the 2026-10-20 finding (the afternoon pool, #208).** *Left open on purpose, recorded rather than fixed:* six of the original 19 Strategist
  topics are broad territory words of exactly the shape the v4.20 finding says produces soft
  output ("mental health and burnout prevention", "digital minimalism and focus", …). They
  have never run, so there is no evidence either way. Read the first weeks of Strategist
  posts and cut the ones that come out soft. *Also open:* `get_recent_posts` fetches
  `limit=10` — about five days — so when a topic comes round the model cannot see its own
  previous take, which is why the repeats are near-verbatim rather than merely similar.

These are judgement calls, not data questions. Engagement at this scale cannot settle any of them — decide by reading the feed.

---

## §4 — The plans

**`docs/REVIEW_blubot_2026-10-19.md`** (added 2026-10-19) — what to take from Shreyas's
BluBot after his 2026-10-17/18 batch (#184–#195). Splits **could** from **should**.
Short version: the should-list is two items — a two-phase pre-broadcast reservation
(we write the dedup entry only *after* delivery, so a crash mid-broadcast can repost)
and feed backoff with automatic recovery on top of the health data we already collect.
Everything else is could-with-a-trigger or rejected. **None of his twelve PRs touches
the binding constraint** (the 2026-10-12 cold read: 2 of 20, Mentor 0 of 10), so none of
it jumps the priority order above.

**`docs/PLAN_provider_agnostic.md`** (added 2026-10-10) — the BHAG: choose which AI
platform writes the bot, plus a failover, and have it report what that choice costs you.
**Planning only; nothing built, nothing queued**, and deliberately *not* ahead of item 4
in the priority order above. Stage 0 (native structured output for the Curator contract)
is the one piece worth doing on its own merits regardless of the rest. Grew out of the
single Anthropic fallback shipped in #180.

**`docs/PLAN_engagement.md`** is parked with the growth goal (§1). Its phase table is in
[`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md).


---

## Rejected

- **Threads (Meta) as a broadcast target.** Rejected multiple times. Don't resurrect.

---

## Changelog

- Entries before v4.31.0 are in [`BACKLOG_ARCHIVE.md`](BACKLOG_ARCHIVE.md).
- 2026-10-20: **v4.31.0 — The images stopped talking.** Release of #197–#206. The headline is a bug that shipped on 2026-04-18 in v4.11.0 and ran **six months**: `build_visual_prompt` handed the no-text/no-people constraint to the model that *writes* the image prompt and never to the model that *draws*, so the constraint was advice to the wrong reader. Post images carried rendered captions the whole time — `UNIT 1` and `COUNTERWEIGHT` on 10-17, `C:\WINDOWS\SYSTEM\VIEW` and `USER: ADNIN > LDAD` on 10-18 — on an account whose images are a brand surface. Nothing in the toolchain was ever going to find it: ruff, mypy, pytest, CodeQL and a paid external security scan were all clean throughout, correctly, because it is neither a security bug nor a type error. **What found it was opening three images.** The constraint now reaches the image model, and the prompt text moved to `FALLBACK_VISUAL_PROMPT` / `VISUAL_PROMPT_CONSTRAINTS` in `src/config.py` so a probe cannot test a drifting copy of it (#199). The rest of the release is the answer to the question that followed — why a scan five days earlier had not prevented this — and the answer is that review effort was aimed at `.py` while the defects were not. Three correctives: `workflow-lint.yml` runs zizmor at `--min-confidence high`, chosen because all 19 current findings are Low and the real P1 (a `${{ inputs.topics }}` template injection this repo wrote itself, in a step holding `GEMINI_API_KEY` and both Cloudflare secrets) is High, so the gate separates signal from noise exactly while leaving every audit switched on; `tests/test_repo_hygiene.py` fails if any state file or sibling is ever tracked, after a test run wrote `seen_articles.json.bak` into a commit and `.gitignore` covered only the bare name — a committed `.bak` is restored on a fresh runner by `_load_json_with_repair_strict`, marked TRUSTED, and overwrites the real Gist (#205); and `image-audit.yml` opens a monthly issue whose body **is** the pictures, with no score and no dashboard, because a number would have read green for all six months (#206). Also in: the broadcast is now reserved in remote state *before* it goes out and settled after, closing a latent repost window where a run that posted and then died would re-select the same story (#198); and the settlement read learned to tell "the claim is absent" from "I could not read the state", which the first version could not and which would have made the guard report the very story it exists to protect (#200). Plus a read of Shreyas's BluBot with a Cloudflare Workers AI image probe, tiered into should/could/rejected rather than a wish list (#197), and the finding itself written down (#202). 1130 → 1326 tests, ruff and mypy clean. **Live-verified**, and unusually all of it within 24 hours of the cut: the image fix on the 2026-10-19 14:30 UTC run at `4d989a3` (a clean balance-scale illustration, no text anywhere) and again on 2026-10-20 14:32 (a scale weighing a rolled document against a boulder, against a post about advisers and owners — no text, no people, no hands); the reservation on the 2026-10-20 07:00 UTC run (`39383701435`), which logged `broadcast_reserved` → `broadcast_settled`, zero ERROR and zero WARNING lines, and none of `broadcast_reservation_not_readable`, `broadcast_claim_release_failed` or `broadcast_release_not_readable`; the image audit by a manual dispatch on 2026-10-19 (`39300876246`) that produced issue #207 with 92 images, where the load-bearing assumption — that GitHub would render `cdn.bsky.app` URLs — was checked rather than assumed, since camo serves them (`200 image/webp 23,788 bytes 1024x1024`, byte-identical to the file opened locally); and `workflow-lint` green on main's own pushes at 17:19 and 18:14 UTC. **Test-covered only:** the two new guards have never fired in anger, which is the point of them — no state file has been committed since `test_repo_hygiene.py` landed, and no High-confidence workflow finding has occurred since the gate went in, so both are proven against reproductions of the original bugs rather than by a live catch. The Cloudflare image probe is a manual workflow the daily path never touches and this release's green runs say nothing about it. The image audit's own defences — the malformed-entry guard, the 60,000-character trim, the string-URL checks — are covered by tests only; the one real dispatch exercised none of them. **Known limit, recorded not closed:** a generated image delivered only to Mastodon (a failed Bluesky blob upload while Mastodon's independent upload succeeds) is outside the audit sheet; it is a sampling gap rather than a blind spot, since the same prompt produces both, and it reopens if a defect is ever found on Mastodon the sheet had missed. **`RELEASING.md` §4 CLOSED 2026-10-21:** the first scheduled run after the merge (`39558964006`, 07:00 UTC) logged `AskFred Engine v4.31.0`. The release is proven end to end. That same run is also the trial's first fallthrough and worth recording here, because a banner check that only reads the banner would have called it clean: `claude-sonnet-5-5` returned 309 then 305 characters against the 300-character limit, failed `validate_summary` on both attempts, and `claude-haiku-5-5` wrote the post that shipped — 17 of the first 18 trial runs were Sonnet, this was the exception, and the failure mode is length compliance rather than availability. Six WARNING lines, no ERROR: the two generation failures, their two `post_length_exceeded` partners, and a `dns_validated_request_blocked` / `feed_fetch_blocked` pair that cost the run the Microsoft Research feed to strict DNS containment (#212). Note for the next release: "no WARNING lines" was asserted about this run before the counts were read, from a grep for a lowercase `level` value that `record.levelname` emits in uppercase — a pattern that cannot match returns zero and reads as health.
- 2026-10-29: **v4.32.0 — The afternoon sounds like Frederik.** Release of #208–#237. The afternoon was hardwired to Mentor, so the Strategist's topics had almost never reached a reader, and 37 of 50 afternoon posts between 2026-09-01 and 10-20 came from a pool of twelve topics. It now alternates Mentor and Strategist by date parity, and the afternoon may now talk about AI, which it never had (#208). The Mentor pool was then rebuilt from Frederik's own articles on frederikvanhecke.com and askfred.be: 21 seeds about working life, change, procurement and adoption, picked by him from a drafted 25, with developer-only topics kept out by a test (#231). The Sonnet trial closed as KEEP on craft grounds (#228), the docs stopped saying Gemini writes (#227), and the afternoon slot moved to 19:00 Brussels for a four-week test that runs to 2026-11-24 (#229, readout in #230). Mention replies now walk the same Sonnet-first chain as the posts, stay in the thread they were made in, and are picked by age (48 hours) instead of by an "unread" flag that flipped whenever the app was opened (#236). Fixes: the DNS pin no longer refuses a resolver's rotated answer, which had been silently dropping The Register and Microsoft Research on some mornings (#212, #216); a corrupt Gist no longer blocks settling a broadcast claim that only the Gist holds (#201, #218); Pillow 13 and certifi 2026.10.19 close nine advisories, one of which needed the Pillow ceiling raised before a fix was even reachable (#214). Internals: `pip-audit` runs daily and a red run opens one self-closing issue (#215, #217), both audit jobs are bounded at ten minutes (#233), weekly grouped Dependabot bumps are back with a lockfile fix-up workflow (#234; #235 was the first and passed untouched), the workflows were swept for the artipacked class (#219), the Gemini reviewer got a repo style guide, with a test that keeps its rules in step with `AGENTS.md` (#220, #222, #224, #225), six small simplifications (#232), and the docs and wiki were caught up with all of the above (#237). 1326 → 1471 tests. **Live-verified** on the three scheduled runs since #236 merged, all on `9f0f998`: `40740977716` (2026-10-28 18:00 UTC, Strategist), `40839037163` (2026-10-29 08:00 UTC, Curator) and `40926629875` (2026-10-29 18:00 UTC, Mentor, a Pioneer post on RFC 1149). All three show `model_used: claude-sonnet-5-5`, Mastodon tags from `claude-haiku-5-5` reviewed by `gemini-3.7-flash`, a generated image, and every line at INFO, with no WARNING or ERROR. The mention pass ran on the new code each time (`interactions_check_started`), so the 48-hour selection is in production. **Test-covered only:** no mention arrived in those windows, so a Sonnet-written reply, the thread-root fix and `mention_reply_chain_exhausted` have not been seen live yet; the DNS-pin and corrupt-Gist fixes are only exercised when a resolver rotates or the Gist is corrupt. **`RELEASING.md` §4 CLOSED 2026-10-30:** the next scheduled run, `41023519962` (2026-10-30 08:00 UTC, Curator, on `5a68e9c`), logged `AskFred Engine v4.32.0`. Sonnet wrote the post and both platforms delivered: `broadcast_settled` says `delivered: true`, which alone only proves one platform did, and the log has no WARNING line at all, so no `bluesky_partial_delivery` or `mastodon_partial_delivery`, one of which a failed platform logs. Haiku proposed the tags with Gemini reviewing. One ERROR line: `metadata_extraction_failed`, a 403 from openai.com on the top candidate's OpenGraph scrape. That cost nothing, because the model wrote about The Register item and the link card was rebuilt for it (`curator_link_realigned`). No mention arrived, so the reply path is still unseen live.
