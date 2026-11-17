"""Phase 4b approval entry point — posts an approved draft or rejects it.

Triggered manually via ``.github/workflows/approve_pending_reply.yml``
(``workflow_dispatch`` only). Inputs:

  - ``DRAFT_ID``: exact UUID of the reviewed pending draft
  - ``APPROVAL_DIGEST``: exact SHA-256 digest shown with that pending draft
  - ``ACTION``: ``approve`` or ``reject``
  - ``REJECT_REASON``: optional free-text reason when ``ACTION=reject``

Approve path: re-fetch the parent post via Bluesky (defensive against
deletion between scan and approval) → persist a posting intent → post the
staged draft as a reply → move the entry from ``pending`` to ``posted``.

Reject path: move from ``pending`` to ``rejected`` with the supplied
reason. No network calls.

Kill-switch: this script is invoked only by its own workflow. Disabling
that workflow stops the approval surface entirely; the daily Curator/
Mentor pipeline is untouched (no shared code path).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from src import proactive
from src.bluesky_session import load_or_login
from src.logger import SafeLogger


async def _run() -> int:
    """Single approval-or-rejection action. Returns shell exit code.

    Returns 0 only when the action succeeded and state persisted.
    Returns 1 on bad input, missing creds, or any failure — callers
    (the workflow UI) want a red X when something didn't go through,
    unlike the scan workflow which always exits 0.
    """
    run_id = os.environ.get(
        "GITHUB_RUN_ID",
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
    )
    SafeLogger.configure(run_id=run_id, platform="proactive")

    draft_id = os.environ.get("DRAFT_ID", "").strip()
    approval_digest = os.environ.get("APPROVAL_DIGEST", "").strip()
    action = os.environ.get("ACTION", "").strip().lower()
    reject_reason = os.environ.get("REJECT_REASON", "").strip() or "manual"

    if action not in ("approve", "reject"):
        SafeLogger.error(
            "proactive_approve_bad_action",
            "ACTION env var must be 'approve' or 'reject'",
            action=action,
        )
        return 1
    if not draft_id:
        SafeLogger.error(
            "proactive_approve_draft_id_missing",
            "DRAFT_ID must identify the exact reviewed draft",
        )
        return 1
    if action == "approve" and (
        draft_id == "first" or not proactive.is_valid_approval_digest(approval_digest)
    ):
        SafeLogger.error(
            "proactive_approve_binding_invalid",
            "Approve requires an exact draft id and lowercase SHA-256 approval digest",
            mutable_selector=draft_id == "first",
        )
        return 1

    SafeLogger.info(
        "proactive_approve_started",
        "Approval workflow run started",
        draft_id=draft_id,
        action=action,
    )

    state, trusted = proactive.load_pending_replies()
    if not trusted:
        # The Gist read failed — `state` is empty because we couldn't read it,
        # not because the draft is gone. Proceeding would (a) report the draft
        # "not found" for the wrong reason and (b) persist an empty state,
        # destroying real pending/posted/rejected history. Abort without saving.
        SafeLogger.error(
            "proactive_approve_state_untrusted",
            "Pending-replies read failed; aborting without save to avoid destroying state",
            draft_id=draft_id,
            action=action,
        )
        return 1
    now = datetime.now(timezone.utc)

    if action == "reject":
        result = proactive.reject_draft(draft_id, state, now, reject_reason)
    else:  # approve
        username = os.environ.get("BLUESKY_USERNAME", "").strip()
        password = os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
        if not username or not password:
            SafeLogger.error(
                "proactive_approve_creds_missing",
                "Bluesky creds missing for approval",
                have_user=bool(username),
                have_pass=bool(password),
            )
            return 1
        try:
            from atproto import AsyncClient
        except ImportError as e:  # pragma: no cover
            SafeLogger.error(
                "proactive_approve_atproto_import_failed",
                "Could not import atproto",
                error_type=type(e).__name__,
                error_msg=str(e)[:200],
            )
            return 1
        bsky = AsyncClient()
        try:
            await load_or_login(bsky, username, password)
        except Exception as e:
            SafeLogger.error(
                "proactive_approve_login_failed",
                "Bluesky login failed",
                error_type=type(e).__name__,
                error_msg=str(e)[:200],
            )
            return 1
        result = await proactive.approve_draft(
            draft_id, approval_digest, bsky, state, now,
        )

    saved = proactive.save_pending_replies(state)
    if not saved:
        # An approve that already POSTED but failed to persist is the
        # dangerous case (Codex review 2026-06-12, finding #2): the reply is
        # live on Bluesky, while the last confirmed remote state is its
        # pre-send posting intent. That intent (or a final write that committed
        # despite a lost response) makes a re-run fail closed, but the operator
        # still needs the URI to reconcile the entry into `posted`.
        # Surface that as a distinct, loud incident with the recovery data.
        # A reject (or a failed approve that never posted) gets the generic
        # save-failure log.
        # Narrowed once, visibly: result is None when no draft matched.
        posted_entry = result if isinstance(result, dict) else {}
        posted_uri = posted_entry.get("posted_uri")
        if action == "approve" and posted_uri:
            # Prefer the UUID recorded with the posted entry. The remote pending
            # entry is keyed by UUID; an operator repairing pending_replies.json
            # needs that id to edit the right entry.
            SafeLogger.error(
                "proactive_posted_but_not_persisted",
                "Reply WAS POSTED but final state did not persist — the durable "
                "pre-send state blocks a re-send; reconcile pending_replies.json "
                "before retrying",
                draft_id=posted_entry.get("id", draft_id),
                posted_uri=posted_uri,
                parent_author=posted_entry.get("parent_author", ""),
            )
        else:
            SafeLogger.error(
                "proactive_approve_state_save_failed",
                "Action completed but state did not persist — inconsistent",
            )
        return 1
    if result is None:
        return 1
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
