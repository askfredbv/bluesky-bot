"""One-shot: push the configured profile bios to Bluesky and Mastodon.

The bot's `update_profile_bio` / `update_profile_bio_mastodon` functions
exist in `src/broadcasters.py` but are not currently invoked from the
main run loop — so config changes to APPROVED_BIO_BSKY /
APPROVED_BIO_MASTODON never reach the live profiles. This script is the
explicit push.

Run:
    python -m scripts.push_bio

Env vars: same as audit_watchlist.py (BLUESKY_USERNAME,
BLUESKY_APP_PASSWORD, MASTODON_ACCESS_TOKEN, MASTODON_API_BASE_URL).
Reads `.env` automatically if python-dotenv finds one.

Idempotent on the wire — Bluesky and Mastodon both accept the same bio
content as a no-op write. Safe to re-run.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from src.broadcasters import update_profile_bio, update_profile_bio_mastodon
from src.config import APPROVED_BIO_BSKY, APPROVED_BIO_MASTODON


def _required(name: str, fallback: Optional[str] = None) -> str:
    raw = (os.environ.get(name) or "").strip()
    if not raw and fallback:
        raw = (os.environ.get(fallback) or "").strip()
    if not raw:
        keys = name + (f" / {fallback}" if fallback else "")
        raise RuntimeError(f"Missing env var: {keys}")
    return raw


async def _run() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    bsky_user = (os.environ.get("BLUESKY_USERNAME") or "askfred.be").strip()
    bsky_pass = _required("BLUESKY_APP_PASSWORD", "BLUESKY_PASSWORD")
    masto_token = (os.environ.get("MASTODON_ACCESS_TOKEN") or "").strip()
    masto_url = (
        os.environ.get("MASTODON_API_BASE_URL") or "https://mastodon.social"
    ).strip()

    # ---- Bluesky ----
    try:
        from atproto import AsyncClient
    except ImportError as exc:  # pragma: no cover
        print(f"[error] atproto import failed: {exc}")
        return 2

    print(f"[bluesky] logging in as {bsky_user} ...")
    bsky = AsyncClient()
    try:
        await bsky.login(bsky_user, bsky_pass)
    except Exception as exc:
        print(f"[error] Bluesky login failed: {type(exc).__name__}: {exc}")
        return 2

    print(f"[bluesky] pushing bio ({len(APPROVED_BIO_BSKY)} chars) ...")
    await update_profile_bio(bsky, APPROVED_BIO_BSKY)
    print("[bluesky] done")

    # ---- Mastodon ----
    if not masto_token:
        print("[mastodon] no token — skipping")
        return 0

    print(f"[mastodon] pushing bio to {masto_url} ({len(APPROVED_BIO_MASTODON)} chars) ...")
    await update_profile_bio_mastodon(masto_token, masto_url, APPROVED_BIO_MASTODON)
    print("[mastodon] done")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
