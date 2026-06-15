"""One-shot audit of candidate handles for the proactive-reply watchlist.

Per `docs/PLAN_engagement.md §4a`. Fetches the last ~10 posts from each
candidate, scores them on five dimensions, and writes a ranked markdown
table to `docs/WATCHLIST_AUDIT.md` for human review.

Run:
    python -m scripts.audit_watchlist

Env vars: same as the main bot (BLUESKY_USERNAME, BLUESKY_APP_PASSWORD,
MASTODON_ACCESS_TOKEN, MASTODON_API_BASE_URL). Reads `.env` automatically
if `python-dotenv` finds one.

Output is *not* committed automatically — review, then `git add` if you
want to keep it in the repo. The script does not change bot behaviour.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence

# Reuse the bot's existing rules so scoring stays aligned with what the
# bot itself considers off-limits.
from src.config import (
    BANNED_HYPE_WORDS,
    BANNED_QUESTION_PATTERNS,
    SECONDARY_TOPICS,
    TOPIC_MAP,
)


@dataclass(frozen=True)
class _AuditCreds:
    """Minimal credential bundle. Bypasses Settings.from_env() because that
    validates GEMINI_API_KEY which the audit doesn't need."""
    bluesky_username: str
    bluesky_password: str
    mastodon_access_token: Optional[str]
    mastodon_api_base_url: str


def _load_audit_creds() -> _AuditCreds:
    """Read just the creds the audit needs. Raises ValueError on missing
    Bluesky creds (the audit can't proceed without them); Mastodon creds
    are optional — that platform is skipped cleanly if absent."""
    bsky_user = (os.environ.get("BLUESKY_USERNAME") or "askfred.be").strip()
    bsky_pass = (
        os.environ.get("BLUESKY_APP_PASSWORD")
        or os.environ.get("BLUESKY_PASSWORD")
        or ""
    ).strip()
    if not bsky_pass:
        raise ValueError(
            "Missing BLUESKY_APP_PASSWORD (or BLUESKY_PASSWORD). "
            "The audit cannot fetch Bluesky posts without it."
        )
    masto_token = (os.environ.get("MASTODON_ACCESS_TOKEN") or "").strip() or None
    masto_url = (
        os.environ.get("MASTODON_API_BASE_URL") or "https://mastodon.social"
    ).strip()
    return _AuditCreds(
        bluesky_username=bsky_user,
        bluesky_password=bsky_pass,
        mastodon_access_token=masto_token,
        mastodon_api_base_url=masto_url,
    )

# Posts per handle to inspect. Plan calls for ~10; a few more is cheap and
# stabilises the percentage-based scores.
POSTS_PER_HANDLE: int = 12

# Cadence is "posts per week" — we count posts in the last 14 days from
# the fetched window, not all-time.
CADENCE_WINDOW_DAYS: int = 14

OUTPUT_PATH = Path("docs/WATCHLIST_AUDIT.md")

# Cap on per-handle error message length. Some failure modes (e.g. a
# Mastodon instance returning a full HTML landing page when the token
# is invalid) bury the audit in noise — truncate hard.
_ERROR_MAX_CHARS: int = 200


def _format_error(exc: BaseException) -> str:
    """Render an exception for the audit's `error` field. Newlines collapsed,
    capped at _ERROR_MAX_CHARS so a 200KB HTML response body can't bloat the
    output file."""
    msg = " ".join(str(exc).split())
    if len(msg) > _ERROR_MAX_CHARS:
        msg = msg[: _ERROR_MAX_CHARS - 1] + "…"
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


# ---------------------------------------------------------------------------
# Pure helpers — kept side-effect-free for direct unit testing.
# ---------------------------------------------------------------------------

# Topic keyword bag built once. SECONDARY_TOPICS contains short phrases like
# "open-source culture and community"; we tokenise to single words so a post
# mentioning "open-source" alone counts as a hit.
_TOPIC_TOKENS: set[str] = {
    tok.lower()
    for words in TOPIC_MAP.values()
    for tok in words
} | {
    word.lower()
    for phrase in SECONDARY_TOPICS
    for word in re.findall(r"[a-zA-Z][a-zA-Z\-]+", phrase)
    if len(word) >= 4  # drop "and", "the", "for", etc.
}


_URL_RE = re.compile(r"https?://\S+")
_BARE_LINK_RE = re.compile(r"^\s*https?://\S+\s*$")


class _HTMLStripper(HTMLParser):
    """Mastodon `content` is HTML-escaped; this strips tags into plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        # Convert <br> and <p> boundaries to spaces so word boundaries survive.
        if tag in {"br", "p"}:
            self._chunks.append(" ")

    def text(self) -> str:
        return " ".join(part.strip() for part in self._chunks if part.strip())


def strip_html(html: str) -> str:
    parser = _HTMLStripper()
    parser.feed(html or "")
    return parser.text()


def topic_fit_score(posts: Sequence[str]) -> float:
    """% of posts mentioning at least one topic-bag token. 0–100."""
    if not posts:
        return 0.0
    hits = sum(1 for p in posts if _has_topic_hit(p))
    return round(100 * hits / len(posts), 1)


def _has_topic_hit(text: str) -> bool:
    lowered = text.lower()
    return any(tok in lowered for tok in _TOPIC_TOKENS)


def voice_compat_score(posts: Sequence[str]) -> float:
    """Inverse of (hype-word + reader-bait-question) hit rate. 0–100.

    Zero offences across the window = 100. Every post containing at least
    one banned phrase = 0. Linear in between.
    """
    if not posts:
        return 0.0
    offending = sum(1 for p in posts if _has_voice_offence(p))
    return round(100 * (1 - offending / len(posts)), 1)


def _has_voice_offence(text: str) -> bool:
    lowered = text.lower()
    if any(word in lowered for word in BANNED_HYPE_WORDS):
        return True
    if any(pattern in lowered for pattern in BANNED_QUESTION_PATTERNS):
        return True
    return False


def reply_opportunity_score(posts: Sequence["NormalisedPost"]) -> float:
    """% posts that are *substantive statements* — not reposts, not bare
    links, more than 30 chars of actual text. 0–100.
    """
    if not posts:
        return 0.0
    statements = sum(1 for p in posts if _is_statement(p))
    return round(100 * statements / len(posts), 1)


def _is_statement(post: "NormalisedPost") -> bool:
    if post.is_repost:
        return False
    text = post.text.strip()
    if not text or _BARE_LINK_RE.match(text):
        return False
    # Strip URLs before measuring length — a tweet that's "Big news! https://…"
    # is barely a statement.
    text_no_urls = _URL_RE.sub("", text).strip()
    return len(text_no_urls) >= 30


def cadence_score(posts: Sequence["NormalisedPost"], now: datetime) -> tuple[float, float]:
    """Returns (score_0_100, posts_per_week_in_window).

    Counts only own posts (not reposts) within the last CADENCE_WINDOW_DAYS.
    Score is clamped: 10+/week → 100, linear below.
    """
    if not posts:
        return 0.0, 0.0
    cutoff = now.timestamp() - CADENCE_WINDOW_DAYS * 86400
    own_in_window = [p for p in posts if not p.is_repost and p.timestamp >= cutoff]
    posts_per_week = len(own_in_window) / (CADENCE_WINDOW_DAYS / 7)
    score = min(100.0, posts_per_week * 10)  # 10 posts/week = 100
    return round(score, 1), round(posts_per_week, 2)


def engagement_substrate_score(posts: Sequence["NormalisedPost"]) -> tuple[float, float]:
    """Returns (score_0_100, avg_replies_plus_reposts_per_post).

    `replies + reposts` matters more than likes — likes are passive consumption,
    replies/reposts are conversation surface. Score clamped at 3 → 100.
    """
    own = [p for p in posts if not p.is_repost]
    if not own:
        return 0.0, 0.0
    avg = sum(p.replies + p.reposts for p in own) / len(own)
    score = min(100.0, avg * (100 / 3))
    return round(score, 1), round(avg, 2)


# ---------------------------------------------------------------------------
# Normalised post + per-handle audit record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalisedPost:
    text: str
    timestamp: float          # unix seconds, UTC
    likes: int
    reposts: int
    replies: int
    is_repost: bool           # True if this is a repost/boost of someone else's post
    permalink: str = ""       # for the sample-post appendix


@dataclass
class HandleAudit:
    platform: str
    handle: str
    posts: List[NormalisedPost] = field(default_factory=list)
    error: Optional[str] = None

    # Computed scores (filled by score_handle).
    topic_fit: float = 0.0
    voice_compat: float = 0.0
    reply_opportunity: float = 0.0
    cadence: float = 0.0
    posts_per_week: float = 0.0
    engagement_substrate: float = 0.0
    avg_engagement: float = 0.0

    @property
    def aggregate(self) -> float:
        return round(
            (self.topic_fit + self.voice_compat + self.reply_opportunity
             + self.cadence + self.engagement_substrate) / 5,
            1,
        )


def score_handle(audit: HandleAudit, now: datetime) -> None:
    """Populate score fields on `audit` from its `posts`. In-place."""
    texts = [p.text for p in audit.posts]
    audit.topic_fit = topic_fit_score(texts)
    audit.voice_compat = voice_compat_score(texts)
    audit.reply_opportunity = reply_opportunity_score(audit.posts)
    audit.cadence, audit.posts_per_week = cadence_score(audit.posts, now)
    audit.engagement_substrate, audit.avg_engagement = engagement_substrate_score(audit.posts)


# ---------------------------------------------------------------------------
# Fetchers — the impure I/O layer.
# ---------------------------------------------------------------------------

async def fetch_bluesky_posts(client: Any, handle: str) -> List[NormalisedPost]:
    """Fetch up to POSTS_PER_HANDLE recent posts for a Bluesky handle."""
    response = await client.app.bsky.feed.get_author_feed(
        {"actor": handle, "limit": POSTS_PER_HANDLE}
    )
    posts: List[NormalisedPost] = []
    for item in response.feed:
        post = item.post
        record = post.record
        text = getattr(record, "text", None)
        if text is None:
            continue
        # `item.reason` is set when this is a repost surfaced via the author
        # feed. Author feeds normally suppress reposts of others, but we
        # check defensively.
        is_repost = getattr(item, "reason", None) is not None
        try:
            ts = datetime.fromisoformat(post.indexed_at.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = datetime.now(timezone.utc).timestamp()
        posts.append(NormalisedPost(
            text=text,
            timestamp=ts,
            likes=int(getattr(post, "like_count", 0) or 0),
            reposts=int(getattr(post, "repost_count", 0) or 0),
            replies=int(getattr(post, "reply_count", 0) or 0),
            is_repost=is_repost,
            permalink=f"https://bsky.app/profile/{handle}/post/{post.uri.rsplit('/', 1)[-1]}",
        ))
    return posts


def fetch_mastodon_posts(mastodon_client: Any, handle: str) -> List[NormalisedPost]:
    """Fetch via federated search on the bot's own Mastodon instance."""
    found = mastodon_client.account_search(handle, limit=2)
    if not found:
        raise RuntimeError(f"Federated search returned nothing for {handle!r}")
    account = found[0]
    statuses = mastodon_client.account_statuses(
        account["id"],
        limit=POSTS_PER_HANDLE,
        exclude_replies=False,
        exclude_reblogs=False,
    )
    posts: List[NormalisedPost] = []
    for status in statuses:
        is_repost = status.get("reblog") is not None
        if is_repost:
            content_html = status["reblog"].get("content", "")
            url = status["reblog"].get("url", "")
        else:
            content_html = status.get("content", "")
            url = status.get("url", "")
        text = strip_html(content_html)
        created_at = status.get("created_at")
        if isinstance(created_at, datetime):
            ts = created_at.timestamp()
        else:
            try:
                ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).timestamp()
            except Exception:
                ts = datetime.now(timezone.utc).timestamp()
        # Mastodon's _own_ engagement counts; for boosts use the reblogged
        # status' counts since that's the conversation surface.
        source = status["reblog"] if is_repost else status
        posts.append(NormalisedPost(
            text=text,
            timestamp=ts,
            likes=int(source.get("favourites_count", 0) or 0),
            reposts=int(source.get("reblogs_count", 0) or 0),
            replies=int(source.get("replies_count", 0) or 0),
            is_repost=is_repost,
            permalink=url,
        ))
    return posts


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_markdown(audits: Iterable[HandleAudit], generated_at: datetime) -> str:
    rows = sorted(
        (a for a in audits if a.error is None),
        key=lambda a: a.aggregate,
        reverse=True,
    )
    skipped = [a for a in audits if a.error is not None]

    out: list[str] = []
    out.append(f"# Watchlist Audit — {generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    out.append("")
    out.append(
        "Phase 4a recon output. Scores are 0–100 per dimension, equal-weighted "
        "into the aggregate. **Read the sample posts before staking a reply** — "
        "a high score means the handle *could* fit, not that any individual "
        "post is reply-worthy."
    )
    out.append("")
    out.append(f"- Posts inspected per handle: {POSTS_PER_HANDLE}")
    out.append(f"- Cadence window: {CADENCE_WINDOW_DAYS} days")
    out.append("")
    out.append("## Ranked")
    out.append("")
    out.append(
        "| Rank | Platform | Handle | Aggregate | Topic fit | Voice | Reply opp. "
        "| Cadence (posts/wk) | Engagement (replies+reposts/post) |"
    )
    out.append(
        "|---:|---|---|---:|---:|---:|---:|---:|---:|"
    )
    for i, a in enumerate(rows, start=1):
        cadence_cell = f"{a.cadence} ({a.posts_per_week}/wk)"
        if a.posts_per_week < 3:
            cadence_cell += " ⚠️"
        engagement_cell = f"{a.engagement_substrate} ({a.avg_engagement})"
        out.append(
            f"| {i} | {a.platform} | `{a.handle}` | **{a.aggregate}** "
            f"| {a.topic_fit} | {a.voice_compat} | {a.reply_opportunity} "
            f"| {cadence_cell} | {engagement_cell} |"
        )
    out.append("")

    if skipped:
        out.append("## Skipped")
        out.append("")
        for a in skipped:
            out.append(f"- `{a.handle}` ({a.platform}) — {a.error}")
        out.append("")

    out.append("## Sample posts")
    out.append("")
    out.append(
        "Three most recent posts per handle, in order. Use these to sanity-"
        "check the scores against reality."
    )
    out.append("")
    for a in rows:
        out.append(f"### `{a.handle}` ({a.platform})")
        out.append("")
        for p in a.posts[:3]:
            badge = "🔁 " if a.platform == "mastodon" and p.is_repost else ""
            preview = p.text.strip().replace("\n", " ")
            if len(preview) > 280:
                preview = preview[:277] + "…"
            line = f"- {badge}{preview}"
            if p.permalink:
                line += f" — [link]({p.permalink})"
            out.append(line)
        out.append("")

    out.append("---")
    out.append("")
    out.append(
        "**Acceptance gate (per plan):** top 5 handles are defensibly good "
        "matches *and* you can point to 2–3 you'd stake a first reply on. "
        "If not, prune the candidate list and rerun."
    )
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def _run() -> int:
    try:
        creds = _load_audit_creds()
    except ValueError as exc:
        print(f"[error] {exc}")
        return 2

    # The real candidates file is gitignored (named handles kept out of the
    # public repo). Fall back to empty lists so a fresh clone runs cleanly —
    # it just audits nobody until you copy watchlist_candidates.example.py to
    # scripts/watchlist_candidates.py and add real handles.
    try:
        from scripts.watchlist_candidates import BLUESKY_CANDIDATES, MASTODON_CANDIDATES
    except ImportError:
        BLUESKY_CANDIDATES, MASTODON_CANDIDATES = [], []
        print("[warn] scripts/watchlist_candidates.py not found — auditing nobody. "
              "Copy scripts/watchlist_candidates.example.py to scripts/watchlist_candidates.py.")

    audits: list[HandleAudit] = []
    now = datetime.now(timezone.utc)

    # ---- Bluesky ----
    try:
        from atproto import AsyncClient
    except ImportError as exc:  # pragma: no cover
        print(f"[error] atproto import failed: {exc}")
        return 2

    bsky = AsyncClient()
    try:
        await bsky.login(
            creds.bluesky_username,
            creds.bluesky_password,
        )
    except Exception as exc:
        print(f"[error] Bluesky login failed: {exc}")
        for handle in BLUESKY_CANDIDATES:
            audits.append(HandleAudit(
                platform="bluesky", handle=handle,
                error=f"login failed: {_format_error(exc)}",
            ))
    else:
        for handle in BLUESKY_CANDIDATES:
            print(f"[bluesky] fetching {handle} ...")
            try:
                posts = await fetch_bluesky_posts(bsky, handle)
                audit = HandleAudit(platform="bluesky", handle=handle, posts=posts)
                score_handle(audit, now)
                audits.append(audit)
            except Exception as exc:
                audits.append(HandleAudit(
                    platform="bluesky", handle=handle,
                    error=_format_error(exc),
                ))

    # ---- Mastodon ----
    if not creds.mastodon_access_token:
        print("[warn] No MASTODON_ACCESS_TOKEN — skipping Mastodon audit")
        for handle in MASTODON_CANDIDATES:
            audits.append(HandleAudit(
                platform="mastodon", handle=handle,
                error="MASTODON_ACCESS_TOKEN missing",
            ))
    else:
        try:
            from mastodon import Mastodon
        except ImportError as exc:  # pragma: no cover
            print(f"[error] mastodon-py import failed: {exc}")
            return 2

        masto = Mastodon(
            access_token=creds.mastodon_access_token,
            api_base_url=creds.mastodon_api_base_url,
        )

        # Preflight: verify the token actually works against the configured
        # instance URL before iterating MASTODON_CANDIDATES. Without this,
        # a wrong api_base_url (e.g. profile URL like .../@user instead of
        # the API base) or an under-scoped token fails opaquely once per
        # candidate with the instance's HTML landing page in the exception
        # body. One clear preflight failure beats five obscure ones.
        try:
            account = await asyncio.to_thread(masto.account_verify_credentials)
            handle = account.get("acct") if isinstance(account, dict) else getattr(account, "acct", None)
            print(f"[mastodon] preflight ok — token belongs to @{handle}")
        except Exception as exc:
            print(f"[error] Mastodon preflight failed: {_format_error(exc)}")
            print(
                f"  Common cause: MASTODON_API_BASE_URL is wrong. Should be the API base "
                f"(e.g. https://mastodon.social), not a profile URL. Currently: "
                f"{creds.mastodon_api_base_url!r}."
            )
            print(
                "  Verify with: curl -H \"Authorization: Bearer $MASTODON_ACCESS_TOKEN\" "
                "$MASTODON_API_BASE_URL/api/v1/accounts/verify_credentials"
            )
            for handle in MASTODON_CANDIDATES:
                audits.append(HandleAudit(
                    platform="mastodon", handle=handle,
                    error=f"preflight failed: {_format_error(exc)}",
                ))
            masto = None

        if masto is None:
            pass  # candidates already populated as skipped above
        else:
            for handle in MASTODON_CANDIDATES:
                print(f"[mastodon] fetching {handle} ...")
                try:
                    posts = await asyncio.to_thread(fetch_mastodon_posts, masto, handle)
                    audit = HandleAudit(platform="mastodon", handle=handle, posts=posts)
                    score_handle(audit, now)
                    audits.append(audit)
                except Exception as exc:
                    audits.append(HandleAudit(
                        platform="mastodon", handle=handle,
                        error=_format_error(exc),
                    ))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_markdown(audits, now), encoding="utf-8")
    print(f"\nWrote {OUTPUT_PATH}")
    print(f"  ranked: {sum(1 for a in audits if a.error is None)}")
    print(f"  skipped: {sum(1 for a in audits if a.error is not None)}")
    return 0


def main() -> int:
    # Honour .env if python-dotenv is installed (it is in requirements.txt).
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:  # pragma: no cover
        pass
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
