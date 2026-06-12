"""Phase 4b daily scan entry point — never posts, only stages drafts.

Wires src/proactive's scan/filter/pick + src/agents.generate_proactive_reply
into a single workflow-dispatchable run:

  1. Load pending state from the Gist
  2. Expire stale drafts (>24h) — handled inside scan_watchlist
  3. Fetch each watchlist account's recent feed, filter, aggregate candidates
  4. Pick the highest-engagement candidate
  5. Ask Gemini for a reply (or SKIP)
  6. Stage the draft for human approval (never posts here)
  7. Persist updated state back to the Gist

The post-the-approved-reply path lives in commit 5's separate
approval workflow.

Kill-switch (per docs/PLAN_engagement.md §4b, ref a2cd1f1):
  - This script does NOT import from main.py
  - main.py does NOT know this script exists
  - Disabling .github/workflows/proactive_scan.yml stops generation
    entirely with zero risk to the daily Curator/Mentor pipeline

The script always exits 0 — proactive replies are best-effort,
non-critical work. A failing workflow on a transient scan failure
would be noise. Real errors get logged at ERROR level for visibility
in the Actions output; the next scheduled run retries.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from src import proactive
from src.agents import generate_proactive_reply
from src.bluesky_session import load_or_login
from src.config import GEMINI_MODEL_PRIORITY, PROACTIVE_REPLY_WATCHLIST
from src.logger import SafeLogger


async def _run() -> int:
    """Single proactive scan run. Returns exit code (always 0)."""
    run_id = os.environ.get(
        "GITHUB_RUN_ID",
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
    )
    SafeLogger.configure(run_id=run_id, platform="proactive")
    SafeLogger.info("proactive_scan_started", "Proactive scan run started")

    username = os.environ.get("BLUESKY_USERNAME", "").strip()
    password = os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not username or not password or not api_key:
        SafeLogger.error(
            "proactive_scan_creds_missing",
            "Required creds missing — skipping run",
            have_bsky_user=bool(username),
            have_bsky_pass=bool(password),
            have_gemini_key=bool(api_key),
        )
        return 0

    try:
        from atproto import AsyncClient
    except ImportError as e:  # pragma: no cover
        SafeLogger.error(
            "proactive_scan_atproto_import_failed",
            "Could not import atproto",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        return 0

    bsky = AsyncClient()
    try:
        await load_or_login(bsky, username, password)
    except Exception as e:
        SafeLogger.error(
            "proactive_scan_login_failed",
            "Bluesky login failed; skipping run",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
        )
        return 0

    state, trusted = proactive.load_pending_replies()
    if not trusted:
        # The Gist read failed — `state` is empty because we couldn't read it,
        # not because there are no drafts. Staging + saving now would overwrite
        # real pending/posted/rejected history and bypass cooldown (the posted
        # history that drives it is gone). Skip the run; next scan retries.
        SafeLogger.error(
            "proactive_scan_state_untrusted",
            "Pending-replies read failed; skipping run to avoid overwriting real state",
        )
        return 0
    pending_before = len(state.get("pending", []))
    now = datetime.now(timezone.utc)

    candidates = await proactive.scan_watchlist(
        bsky, PROACTIVE_REPLY_WATCHLIST, state, now,
    )
    expiry_mutated = len(state.get("pending", [])) != pending_before

    if not candidates:
        SafeLogger.info(
            "proactive_scan_no_candidates",
            "No candidates passed filters; nothing to stage",
            expiry_mutated=expiry_mutated,
        )
        if expiry_mutated:
            proactive.save_pending_replies(state)
        return 0

    candidate = proactive.pick_reply_candidate(candidates)
    assert candidate is not None  # candidates non-empty by the check above
    SafeLogger.info(
        "proactive_scan_candidate_picked",
        "Top reply candidate selected",
        parent_author=candidate["parent_author"],
        engagement=candidate["engagement"],
        total_candidates=len(candidates),
    )

    draft_reply = await generate_proactive_reply(
        api_key=api_key,
        parent_author=candidate["parent_author"],
        parent_text=candidate["parent_text"],
        model_priority=GEMINI_MODEL_PRIORITY,
    )

    if draft_reply is None:
        SafeLogger.info(
            "proactive_scan_no_draft",
            "Model returned SKIP or exhausted chain; nothing staged this run",
            parent_author=candidate["parent_author"],
        )
        if expiry_mutated:
            proactive.save_pending_replies(state)
        return 0

    staged = proactive.stage_draft_reply(candidate, draft_reply, state, now)
    saved = proactive.save_pending_replies(state)
    SafeLogger.info(
        "proactive_scan_completed",
        "Draft staged and state persisted",
        draft_id=staged["id"],
        parent_author=staged["parent_author"],
        draft_length=len(draft_reply),
        gist_save_ok=saved,
    )
    return 0


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:  # pragma: no cover
        pass
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
