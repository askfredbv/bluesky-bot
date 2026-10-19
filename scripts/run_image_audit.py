"""Contact sheet of the images the bot has posted — built to be *looked at*.

The image-prompt bug (shipped 2026-04-18, found 2026-10-19) put rendered
captions into post images for six months. One read ``USER: ADNIN > LDAD``.
Throughout, ruff was clean, mypy was clean, pytest was green, CodeQL found
nothing and a paid external security scan found nothing — because it is not a
security bug and not a type error. It is "this instruction was given to the
wrong model", and the only instrument that can see it is a pair of eyes.

What found it was opening three images.

So this script prints a markdown contact sheet: the pictures, the date, the
post text, a link. The ``image-audit`` workflow turns it into a GitHub issue,
where the images render inline — the issue *is* the pictures, not a report
about them. That shape is deliberate. A score, a dashboard or a pass/fail
metric here would repeat exactly the mistake ``docs/RETRO_2026-05-08.md``
exists to teach: instrumentation standing in for reading. There is no metric
in the output and there should never be one.

    python -m scripts.run_image_audit --limit 60 > body.md
    python -m scripts.run_image_audit --out images/     # also save the files

Reads nothing secret: the feed and the images are public. Best-effort — it
always exits 0, because a flaky audit should not page anyone.

Known gap, recorded rather than closed: this reads Bluesky only, so an image
that reached Mastodon alone is not in the sheet. That happens when the Bluesky
blob upload fails (``post_to_bluesky`` logs ``image_upload_failed`` and posts
without the embed) while Mastodon's independent upload succeeds. It is a
sampling gap, not a blind spot — the same prompt produces both, so a defect in
the generator shows on the Bluesky side on other days — and closing it would
mean a second API path and cross-platform dedup for a rare case. Revisit if a
defect is ever found on Mastodon that this sheet had missed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

#: The bot's own handle. Hardcoded like ``scripts/run_voice_audit.py`` — this
#: audits one account and the feed is public, so there is no credential to
#: read and nothing to configure.
DEFAULT_ACTOR = "askfred.be"

_FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"

#: One page only — the endpoint caps ``limit`` at 100, and paging would add a
#: failure mode for range nobody is asking for.
_MAX_LIMIT = 100

#: The whole page, deliberately. Two posts a day over a 31-day month is 62, so
#: a 60-post window would drop the oldest two *permanently*: the job runs on
#: the 1st and does not page back to the previous audit's boundary, so what
#: falls off the end is never looked at. 100 is ~50 days, which overlaps the
#: previous audit instead of leaving a hole. Seeing a picture twice costs
#: nothing; never seeing it is the failure this whole workflow exists to stop.
DEFAULT_LIMIT = _MAX_LIMIT

#: Images are only ever fetched from the Bluesky CDN. The URLs come from the
#: AppView rather than from a feed we scraped, so this is belt-and-braces, but
#: an exact-host allowlist is cheaper to reason about than a general SSRF
#: check and it is the only host that can legitimately appear here.
_IMAGE_HOST = "cdn.bsky.app"

#: Enough of the post to recognise which one a picture belongs to.
_TEXT_PREVIEW_CHARS = 180

#: A generated illustration is ~1 MB before compression; 8 MB is slack.
_IMAGE_BYTE_CAP = 8 * 1024 * 1024

#: GitHub rejects an issue body over 65,536 characters outright, and a rejected
#: body is a month with no audit at all — the same silent skip as a crash. A
#: live run at ``limit=100`` (44 illustrations, 48 cards) came to ~45,000, so
#: this is a backstop with headroom, not a squeeze. Measured, not assumed:
#: trusting "it will fit" is the shape of reasoning that cost six months.
_BODY_BUDGET_CHARS = 60_000


@dataclass(frozen=True)
class AuditItem:
    """One picture in the feed, with just enough to find the post again."""

    created_at: str
    text: str
    image_url: str
    post_url: str
    source_uri: Optional[str] = None


def _as_dict(value: Any) -> Dict[str, Any]:
    """``value`` if it is a mapping, else an empty one.

    Every field below comes off a public API response, so a shape that is not
    the documented one is an input, not a bug to crash on.
    """
    return value if isinstance(value, dict) else {}


def _post_web_url(at_uri: str, handle: str) -> str:
    """Turn ``at://did:plc:…/app.bsky.feed.post/<rkey>`` into a bsky.app link.

    Returns an empty string for anything that is not shaped like a post URI,
    so a malformed record costs a missing link, not the whole report.
    """
    if not at_uri or "/app.bsky.feed.post/" not in at_uri:
        return ""
    rkey = at_uri.rsplit("/", 1)[-1]
    if not rkey:
        return ""
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


def _preview(text: str) -> str:
    """One-line, length-capped version of the post text."""
    flat = " ".join((text or "").split())
    if len(flat) <= _TEXT_PREVIEW_CHARS:
        return flat
    return flat[: _TEXT_PREVIEW_CHARS - 1].rstrip() + "…"


def classify_feed(
    feed: List[Dict[str, Any]], handle: str
) -> Tuple[List[AuditItem], List[AuditItem]]:
    """Split a getAuthorFeed response into illustrations and link cards.

    The two shapes are different products of the same prompt path, and the
    split matters when looking:

    * ``app.bsky.embed.images`` — the afternoon Mentor/Strategist picture.
      Always generated by the image model. Unambiguous.
    * ``app.bsky.embed.external`` with a thumb — a Curator card. The thumb is
      the article's own OG image, *or* one the bot generated because the
      article had none (``_apply_curator_fallback_image``). **The public feed
      does not record which**, so these are reported as one group and labelled
      honestly rather than filtered on a guess.

    Reposts are skipped (``item["reason"]``): the picture would be somebody
    else's. Replies are already excluded by the feed filter.
    """
    illustrations: List[AuditItem] = []
    cards: List[AuditItem] = []

    for item in feed or []:
        # One malformed entry must cost one picture, not the month. An
        # AttributeError escaping here would fail the workflow step under
        # `set -e`, so there would be no artifact and no issue — the audit
        # silently skipped for a null record. AGENTS.md §4: best-effort work
        # catches, logs and continues.
        try:
            entry = _as_dict(item)
            if entry.get("reason"):
                continue
            post = _as_dict(entry.get("post"))
            record = _as_dict(post.get("record"))
            embed = _as_dict(post.get("embed"))
            # The leaf fields are validated here too, not just the containers.
            # ``created_at`` is the one value consumed later — ``_render_item``
            # slices it, outside any guard — so a non-string timestamp would
            # pass this loop and take the whole sheet down with a TypeError.
            # ``text`` and ``uri`` are consumed on this line, so a bad shape in
            # either lands in the except below and costs only its own entry.
            stamp = record.get("createdAt") or post.get("indexedAt") or ""
            shared = {
                "created_at": stamp if isinstance(stamp, str) else "",
                "text": _preview(record.get("text") or ""),
                "post_url": _post_web_url(post.get("uri") or "", handle),
            }

            embed_type = embed.get("$type") or ""
            if embed_type.startswith("app.bsky.embed.images"):
                images = embed.get("images")
                for image in images if isinstance(images, list) else []:
                    url = _as_dict(image).get("fullsize") or _as_dict(image).get("thumb")
                    if url:
                        illustrations.append(AuditItem(image_url=url, **shared))
            elif embed_type.startswith("app.bsky.embed.external"):
                external = _as_dict(embed.get("external"))
                thumb = external.get("thumb") or ""
                if thumb:
                    cards.append(AuditItem(
                        image_url=thumb, source_uri=external.get("uri") or None,
                        **shared))
        except Exception as exc:  # noqa: BLE001 - one bad entry, not the run
            print(f"skipped a malformed feed entry: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)

    return illustrations, cards


def _render_item(item: AuditItem) -> str:
    """One entry: the date line, the picture, the words it went out with."""
    stamp = item.created_at[:16].replace("T", " ") or "date unknown"
    head = f"**{stamp} UTC**"
    if item.post_url:
        head += f" · [open the post]({item.post_url})"
    block = [head, "", f"![]({item.image_url})", ""]
    if item.text:
        block.append(f"> {item.text}")
        block.append("")
    if item.source_uri:
        block.append(f"Article: {item.source_uri}")
        block.append("")
    return "\n".join(block)


def _trim_to_budget(
    illustrations: List[AuditItem], cards: List[AuditItem]
) -> Tuple[List[AuditItem], List[AuditItem], int]:
    """Drop the oldest pictures if the sheet would not fit in a GitHub issue.

    Link cards go first and the illustrations last: the generated images are
    the surface the bug was on, and a publisher's thumbnail is the part a
    reader can skip. Every picture is still in the artifact either way, and the
    report says how many it is not showing rather than quietly ending early.
    """
    kept_illustrations = list(illustrations)
    kept_cards = list(cards)
    total = sum(len(_render_item(i)) for i in kept_illustrations + kept_cards)
    dropped = 0
    while total > _BODY_BUDGET_CHARS and (kept_cards or len(kept_illustrations) > 1):
        victim = kept_cards.pop() if kept_cards else kept_illustrations.pop()
        total -= len(_render_item(victim))
        dropped += 1
    return kept_illustrations, kept_cards, dropped


def render_report(
    illustrations: List[AuditItem], cards: List[AuditItem], posts_scanned: int
) -> str:
    """The contact sheet. Pictures, no score — see the module docstring."""
    illustrations, cards, dropped = _trim_to_budget(illustrations, cards)
    out: List[str] = [
        "## The pictures the bot posted — look at them",
        "",
        f"_{len(illustrations)} generated illustrations and {len(cards)} link-card "
        f"thumbnails, from the last {posts_scanned} posts as the public feed serves "
        "them. Nothing here is scored or graded. Scroll the pictures._",
        "",
        "**The question: would you have posted this picture?** It has a hard answer "
        "for one of the two sections below and a judgement answer for the other; each "
        "says which, because applying the strict rule to a publisher's own artwork "
        "would manufacture a finding every month and teach you to ignore the rule.",
        "",
        "Why this issue exists: a prompt bug put captions into these images from "
        "2026-04-18 to 2026-10-19. `USER: ADNIN > LDAD` went out on 2026-10-18. For "
        "six months ruff, mypy, pytest, CodeQL and a paid security scan all passed. "
        "Opening three images is what found it. That is also why there is no "
        "dashboard here — a number would have read as green the whole time.",
        "",
    ]

    if not illustrations and not cards:
        out += [
            "---",
            "",
            "No pictures in the window. That is itself worth a look: either the "
            "`IMAGE_GENERATION_PROBABILITY` path has stopped producing anything, or "
            "the feed query returned nothing.",
            "",
        ]
        return "\n".join(out)

    if illustrations:
        out += [
            "---",
            "",
            f"### Generated illustrations ({len(illustrations)})",
            "",
            "Every one of these came out of the image model, and this is the surface "
            "the bug was on. **The hard rule applies here**: rendered text of any kind "
            "— words, letters, numbers, labels, captions, signage, a watermark — is a "
            "defect, and so are people, hands and faces. The prompt forbids all of "
            "them (`VISUAL_PROMPT_CONSTRAINTS`, `src/config.py`), so anything you see "
            "here is the prompt not reaching the model.",
            "",
        ]
        out += [_render_item(item) for item in illustrations]

    if cards:
        out += [
            "---",
            "",
            f"### Link-card thumbnails ({len(cards)})",
            "",
            "A Curator card carries the article's own thumbnail — or one the bot "
            "generated, when the article had none. **The public feed does not say "
            "which is which**, so both are here, and the hard rule above does not "
            "apply to the section: a publisher's own image is entitled to its text, "
            "its logo and the people in it. Ask the looser question instead — does "
            "this picture embarrass the post it rode with? A flat illustration that "
            "carries lettering is the one to flag; it is ours, and you can tell by "
            "looking.",
            "",
        ]
        out += [_render_item(item) for item in cards]

    if dropped:
        out += [
            "---",
            "",
            f"**{dropped} older picture(s) are not shown above.** The sheet reached "
            "GitHub's issue-body limit, and a body over it is rejected outright — "
            "which would have meant no audit at all this month. Every picture is in "
            "the run's `post-images` artifact.",
            "",
        ]

    out += [
        "---",
        "",
        "Close this issue once you have looked. If a picture is wrong, open a bug "
        "and name the date — the point of the cadence is that a defect waits weeks, "
        "not months.",
        "",
    ]
    return "\n".join(out)


def fetch_feed(actor: str, limit: int) -> List[Dict[str, Any]]:
    """The account's recent original posts, from the public AppView."""
    resp = httpx.get(
        _FEED_URL,
        params={
            "actor": actor,
            "limit": max(1, min(limit, _MAX_LIMIT)),
            "filter": "posts_no_replies",
        },
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    feed = payload.get("feed") if isinstance(payload, dict) else None
    if not isinstance(feed, list):
        # An unreadable answer, not a quiet month. Returning [] here would
        # print "No pictures in the window", which reads as a clean audit of a
        # month with no images — when what actually happened is that the
        # endpoint changed shape and nobody noticed. ``main`` turns this into
        # the explicit "could not read the feed" result instead, which says
        # that nothing is known either way. AGENTS.md §4: the best-effort half
        # is each entry; the response itself is the part to fail loud on.
        raise ValueError(
            f"response carries no list-shaped 'feed' (got "
            f"{type(feed).__name__})")
    return feed


def save_images(items: List[AuditItem], out_dir: Path) -> int:
    """Write the pictures to ``out_dir`` so the workflow can attach them.

    The issue is the primary surface; these files are the durable copy, for
    full resolution and for after a post is deleted (the issue hot-links the
    CDN). Strictly best-effort: one failed download must not cost the report,
    which has already been printed by the time this runs.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for index, item in enumerate(items, start=1):
        host = (urlparse(item.image_url).hostname or "").lower()
        if host != _IMAGE_HOST:
            print(f"skipped (unexpected host {host!r}): {item.image_url}",
                  file=sys.stderr)
            continue
        try:
            resp = httpx.get(item.image_url, timeout=30)
            resp.raise_for_status()
            data = resp.content[:_IMAGE_BYTE_CAP]
        except Exception as exc:  # noqa: BLE001 - best-effort, message captured
            print(f"download failed for {item.image_url}: "
                  f"{type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
            continue
        stamp = (item.created_at[:10] or "undated").replace(":", "-")
        (out_dir / f"{index:02d}_{stamp}.jpg").write_bytes(data)
        saved += 1
    return saved


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default=DEFAULT_ACTOR,
                        help="Bluesky handle to audit (default: %(default)s)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"posts to scan, max {_MAX_LIMIT} (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=None,
                        help="also save the images to this directory")
    args = parser.parse_args(argv)

    try:
        feed = fetch_feed(args.actor, args.limit)
    except Exception as exc:  # noqa: BLE001 - best-effort entry point
        print(f"## Image audit — could not read the feed\n\n"
              f"`{type(exc).__name__}: {str(exc)[:200]}`\n\n"
              f"The public AppView did not answer, so there are no pictures to "
              f"look at this time. Nothing is known about the images either way.")
        return 0

    illustrations, cards = classify_feed(feed, args.actor)
    print(render_report(illustrations, cards, len(feed)))

    if args.out is not None:
        saved = save_images(illustrations + cards, args.out)
        print(f"saved {saved} image(s) to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
