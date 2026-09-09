"""Diagnostic: see what discovery tags the tagger actually picks.

Read-only — posts nothing, changes nothing. Runs the real tagging call against
the bot's own recent live posts and prints the tags beside each one, so tag
quality can be judged on real text before it ships to the feed.

Why this exists: the tagger's whole value is whether it names a tag someone
browsing that tag would want to find. That is a judgement call about model
output, and unit tests mock the model, so they cannot answer it. The
GEMINI_API_KEY is IP-restricted to the GitHub runners, so this is the only
place the probe is truthful — same rationale as model-discovery.yml and
image-probe.yml. Trigger from the Actions UI.

Reading the per-post output, three stages so a lost tag can be blamed on the
right gate: `raw` is what the model proposed, `sanitized` is what the lexical
gate allowed (raw and sanitized come from the SAME call, so their difference is
attributable to sanitizing alone), and `reviewed` is what the semantic pass
kept. `ends` shows the suffix a Mastodon reader would actually see, after the
broadcaster's ceiling and length handling. Each post runs under the production
deadline, so `retention` is not optimistic about timing.

Then a controlled set with known answers, because retention alone cannot
validate the reviewer. Negatives must be DROPPED, positives must be KEPT, and a
verdict the parser refuses is reported as INVALID rather than scored as a
rejection — otherwise a model returning junk would post a perfect record while
making no decisions at all.

Calls the underlying functions directly; they carry no enablement gate, so this
stays truthful while MASTODON_TAGS_ENABLED is False in production.
"""
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from typing import Any, Dict, List

from src.agents import (
    request_mastodon_tags, review_mastodon_tags, _select_tag_models,
    filter_available_models,
)
from src.broadcasters import apply_mastodon_tags
from src.config import (
    GEMINI_MODEL_PRIORITY, MASTODON_TAGS_EXCLUDED, MAX_HASHTAGS_PER_POST,
    MASTODON_TAGS_TIMEOUT_SECONDS,
)

# The bot's own feed, via the public read-only AppView (no auth needed).
_FEED_URL = (
    "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
    "?actor=askfred.be&limit={limit}&filter=posts_no_replies"
)

# Fallback corpus if the feed cannot be reached, so the probe still says
# something useful about the prompt. Shapes mirror the three modes.
_FALLBACK_POSTS = [
    "NVIDIA's next chip is a packaging story, not a silicon one. The real "
    "constraint is CoWoS capacity, not transistor density.",
    "The half-life of internal documentation is brutal. I have seen more wikis "
    "become graveyards than reference works.",
    "On this day in 1969, the first ARPANET message crashed after two letters. "
    "They got 'LO' out of 'LOGIN'.",
    "A court has ruled that published model weights are not a trade secret. "
    "That is a bigger deal for open-weight releases than the headline suggests.",
]

_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")


def _fetch_recent_posts(limit: int) -> List[str]:
    """Pull the bot's recent post text from the public Bluesky AppView."""
    req = urllib.request.Request(
        _FEED_URL.format(limit=limit),
        headers={"User-Agent": "askfred-bot-tag-probe/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data: Dict[str, Any] = json.loads(resp.read().decode())
    posts: List[str] = []
    for item in data.get("feed", []):
        record = (item.get("post") or {}).get("record") or {}
        text = (record.get("text") or "").strip()
        # Skip thread continuations; the tagger only ever sees a root post.
        if text and (record.get("reply") is None):
            posts.append(text)
    return posts


async def _probe_one(
    key: str, proposer: str, reviewer: str, idx: int, post: str
) -> bool:
    """Print one post's tagging result. Returns True if it ended up tagged.

    Shows all three stages so a lost tag can be attributed to the right gate:
    what the model proposed, what the lexical sanitizer allowed, and what the
    semantic reviewer kept.
    """
    preview = post.replace("\n", " ")
    if len(preview) > 150:
        preview = preview[:149] + "…"
    print(f"[{idx}] {preview}")

    spent = len(_HASHTAG_RE.findall(post))
    allowance = MAX_HASHTAGS_PER_POST - spent
    if allowance <= 0:
        print(f"    tags: (none — the post already spends the "
              f"{MAX_HASHTAGS_PER_POST}-hashtag ceiling)\n")
        return False

    # raw and candidates come from ONE call, so the gap between them is
    # attributable to the sanitizer and nothing else.
    try:
        raw, candidates = await request_mastodon_tags(
            key, post, proposer, allowance
        )
    except Exception as exc:
        print(f"    FAILED (propose) {type(exc).__name__}: {str(exc)[:140]}\n")
        return False

    print(f"    raw       : {raw}")
    print(f"    sanitized : {' '.join(candidates) if candidates else '(none)'}")

    try:
        decisions, tags = await review_mastodon_tags(
            key, post, candidates, reviewer
        )
    except Exception as exc:
        print(f"    FAILED (review) {type(exc).__name__}: {str(exc)[:140]}\n")
        return False

    if decisions is None:
        # Parseable JSON, invalid decision set. Fails closed like a rejection,
        # but it is NOT reviewer judgment — reporting it as "dropped" would
        # credit the reviewer for an answer it never gave (Codex review,
        # 2026-09-09; the same distinction _probe_adversarial already makes).
        print("    reviewed  : INVALID (unparseable verdict — not a judgement)")
        print("    ends      : (unchanged)\n")
        return False

    dropped = [t for t in candidates if t not in tags]
    print(f"    reviewed  : {' '.join(tags) if tags else '(none)'}"
          f"{'   dropped: ' + ' '.join(dropped) if dropped else ''}")
    # What a Mastodon reader would actually see, after the broadcaster's
    # dedup / ceiling / length handling.
    suffix = apply_mastodon_tags([post], tags)[0][len(post):]
    print(f"    ends      : {suffix!r}\n" if suffix else "    ends      : (unchanged)\n")
    return bool(tags)


async def _probe_adversarial(key: str, reviewer: str) -> tuple:
    """Measure the reviewer's DECISION quality, both directions.

    Ten posts the model tagged sensibly say nothing about what it does with a
    bad tag, and rejection is the entire reason this pass exists. But counting
    "no tags came back" as a correct rejection would let a model returning junk
    score a perfect record while making no decisions at all — so a response the
    parser refuses is reported as `INVALID`, never as a rejection (Codex
    reviews, 2026-09-09).

    Positive controls are included for the same reason in reverse: a reviewer
    that drops everything would look flawless against negatives alone.

    Returns (valid_correct, invalid_responses, total).
    """
    # (post, tag, must_keep, why)
    cases = [
        ("A migration rarely stalls on the data pipeline. It stalls on "
         "decommissioning. As long as the legacy system stays reachable, teams "
         "quietly keep dual-writing to it.",
         "#NVIDIA", False, "company the post never mentions"),
        ("Teams keep buying observability tools and then routing half their "
         "services around them. The gap is ownership, not tooling.",
         "#Datadog", False, "plausible vendor the post never names"),
        ("A clever workaround feels like borrowing time. Adjacent systems adapt "
         "to its quirks rather than the intended design.",
         "#QuantumComputing", False, "invented specificity"),
        # Was "#BestPractices" on this post, labelled must-DROP because the
        # prose observes an ownership failure without advocating practice. The
        # reviewer kept it in three consecutive runs, and on adjudication that
        # KEEP is defensible: a reader can reasonably take the post as a lesson
        # about operational practice. The gold label was too contestable to
        # carry evidential weight, so the case was measuring my editorial
        # preference rather than the safeguard (Codex, 2026-09-09).
        #
        # Replaced with a HARDER case on the same post, not an easier one:
        # Kubernetes does not establish that Google Cloud was involved, so this
        # tests the leap from a related technology to an entity the post never
        # names -- the actual risk this pass exists to stop.
        ("Kubernetes autoscaling worked exactly as configured. The configuration "
         "was the problem, and nobody owned it.",
         "#GoogleCloud", False, "entity leap from a related technology"),
        # ...but ADDED rather than swapped, because dropping the framing case
        # would have left the set with no framing control at all: a reviewer
        # that reliably refuses entity leaps while accepting praise or
        # endorsement would then score a clean sweep and suppress this probe's
        # do-not-enable warning, even though AGENTS.md #7 forbids a tag that
        # introduces framing the prose does not carry (Codex review, 2026-09-09).
        #
        # Unambiguous where "#BestPractices" was contestable: the prose reports
        # a tool quietly losing data, so an endorsement tag is not a reading
        # anyone could defend.
        ("The vendor's migration tooling handled 80% of the tables and silently "
         "skipped the rest. We found out in production.",
         "#Recommended", False, "endorsement the prose contradicts"),
        # Positive controls: a reviewer that drops everything must not pass.
        ("Alan Kay coined \"object-oriented\", but later regretted the choice. "
         "He cared about messaging between objects, not classes.",
         "#OOP", True, "acronym for the post's actual subject"),
        ("The Edge add-ons store is drowning in AI-generated extensions, forcing "
         "Microsoft to build automated triage for the review queue.",
         "#BrowserExtensions", True, "the subject, by a conventional name"),
        ("NVIDIA's next chip is a packaging story, not a silicon one. The "
         "constraint is CoWoS capacity, not transistor density.",
         "#NVIDIA", True, "company the post is genuinely about"),
    ]
    print("=" * 68)
    print("decision quality: negatives must DROP, positives must KEEP")
    print("=" * 68)
    correct = invalid = 0
    for post, tag, must_keep, why in cases:
        want = "KEEP" if must_keep else "DROP"
        try:
            decisions, kept = await review_mastodon_tags(
                key, post, [tag], reviewer
            )
        except Exception as exc:
            invalid += 1
            print(f"  {tag:<20} want {want:<4}  ERROR {type(exc).__name__}: "
                  f"{str(exc)[:60]}")
            continue
        if decisions is None:
            # Fail-closed, but NOT a semantic decision. Never scored as correct.
            invalid += 1
            print(f"  {tag:<20} want {want:<4}  INVALID (unparseable verdict)  "
                  f"[{why}]")
            continue
        got_keep = bool(kept)
        ok = got_keep == must_keep
        correct += ok
        print(f"  {tag:<20} want {want:<4}  got {'KEEP' if got_keep else 'DROP':<4}  "
              f"{'ok' if ok else 'WRONG':<5} [{why}]")
    print(f"\n  correct {correct}/{len(cases)}, invalid responses {invalid}\n")
    return correct, invalid, len(cases)


async def _run(posts: List[str]) -> int:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        print("GEMINI_API_KEY not set", file=sys.stderr)
        return 1

    # Select from the FILTERED chain, exactly as main.py does: production
    # passes filter_available_models' output into tagging, so probing the
    # unfiltered list could validate a pairing production never uses
    # (Codex review, 2026-09-09).
    active = await filter_available_models(key, GEMINI_MODEL_PRIORITY)
    proposer, reviewer = _select_tag_models(active)
    if reviewer is None:
        print("No distinct reviewer model is reachable with this key, so "
              "production would post untagged. Nothing to probe.",
              file=sys.stderr)
        return 1
    print(f"proposer: {proposer}")
    print(f"reviewer: {reviewer} (cross-model)")
    print(f"excluded: {', '.join(MASTODON_TAGS_EXCLUDED)}")
    print(f"ceiling:  {MAX_HASHTAGS_PER_POST} per post, shared with the "
          f"generated text\n")

    print(f"budget:   {MASTODON_TAGS_TIMEOUT_SECONDS}s shared by both calls "
          f"(production deadline, applied below)\n")

    tagged = over_budget = 0
    for idx, post in enumerate(posts, 1):
        started = time.perf_counter()
        try:
            # Apply the PRODUCTION deadline. Without it the probe reports
            # retention production would never achieve, because two calls that
            # comfortably finish untimed can still blow the shared budget
            # (Codex review, 2026-09-09).
            got = await asyncio.wait_for(
                _probe_one(key, proposer, reviewer, idx, post),
                timeout=MASTODON_TAGS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            over_budget += 1
            print(f"    OVER BUDGET (>{MASTODON_TAGS_TIMEOUT_SECONDS}s) — "
                  f"production would ship this untagged\n")
            continue
        print(f"    elapsed   : {time.perf_counter() - started:.1f}s\n")
        if got:
            tagged += 1

    correct, invalid, adversarial_total = await _probe_adversarial(key, reviewer)

    total = len(posts)
    print("=" * 68)
    print(f"retention : {tagged}/{total} real posts got tags "
          f"({over_budget} exceeded the budget)")
    print(f"decisions : {correct}/{adversarial_total} correct, "
          f"{invalid} unparseable")
    print("=" * 68)
    if tagged == 0:
        print("  NOTHING got tagged — the gates are too tight to be useful.")
    if correct < adversarial_total:
        print("  The reviewer got a controlled case wrong. Do NOT flip "
              "MASTODON_TAGS_ENABLED until that is understood.")
    if invalid:
        print("  Some verdicts were unparseable. Those fail closed, but they "
              "are not evidence the reviewer judges well.")
    return 0


def main() -> int:
    limit = int(os.environ.get("PROBE_POST_LIMIT", "10"))
    try:
        posts = _fetch_recent_posts(limit)
        source = "live Bluesky feed"
    except Exception as exc:
        print(f"(feed fetch failed: {type(exc).__name__}: {str(exc)[:120]} "
              f"— falling back to sample posts)")
        posts, source = list(_FALLBACK_POSTS), "built-in samples"
    if not posts:
        posts, source = list(_FALLBACK_POSTS), "built-in samples"
    print(f"source: {source} ({len(posts)} posts)\n")
    return asyncio.run(_run(posts))


if __name__ == "__main__":
    raise SystemExit(main())
