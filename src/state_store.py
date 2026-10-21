"""Persistent state IO for the bot: remote key-value store, private-Gist
state (with the trusted/untrusted-empty distinction), atomic local writes with
corruption repair, and the seen-articles / replied-to ledgers.

Extracted from src/utils.py so that src.metrics can import these helpers at
module top without the src.utils <-> src.metrics circular dependency (utils
still imports FeedFetchResult from metrics; nothing here imports utils).
"""
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

import httpx

from src.config import (
    MAX_BROADCAST_RESERVATIONS,
    PIONEER_COOLDOWN_DAYS,
    REPLIED_FILE,
    SEEN_FILE,
    SEEN_LINKS_WINDOW,
)
from src.file_lock import file_lock
from src.logger import SafeLogger
from src.net_safety import canonical_url

T = TypeVar("T")
STATE_STORE_TIMEOUT_SECONDS = 10.0


def _state_store_url_for_key(key: str) -> Optional[str]:
    base_url = os.environ.get("STATE_STORE_URL", "").strip()
    if not base_url:
        return None
    if "{key}" in base_url:
        return base_url.format(key=key)
    return f"{base_url.rstrip('/')}/{key}"
def _state_store_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    token = os.environ.get("STATE_STORE_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
def _load_state_from_store(key: str) -> Optional[Any]:
    endpoint = _state_store_url_for_key(key)
    if not endpoint:
        return None
    try:
        response = httpx.get(endpoint, headers=_state_store_headers(), timeout=STATE_STORE_TIMEOUT_SECONDS)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "value" in data:
            return data["value"]
        return data
    except Exception as e:
        SafeLogger.warn("state_store_read_failed", "Remote state read failed", error_type=type(e).__name__, state_key=key)
        return None
# Statuses that mean the value is durably stored. Deliberately strict, because
# this return value GATES the local-file fallback in save_seen_articles /
# save_replied_to: a wrong True there means the state is written nowhere at all.
# 202 Accepted is excluded on purpose — queued is not stored. A false negative
# costs one redundant local write; a false positive costs the state.
_STATE_STORE_WRITE_OK = frozenset({200, 201, 204})

# PUT rejected as a method: the expected prelude to the POST attempt, not a
# failure worth logging.
_STATE_STORE_PUT_METHOD_REJECTED = 405


def _save_state_to_store(key: str, data: Any) -> bool:
    endpoint = _state_store_url_for_key(key)
    if not endpoint:
        return False
    payload = {"value": data}
    try:
        response = httpx.put(
            endpoint,
            headers={**_state_store_headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=STATE_STORE_TIMEOUT_SECONDS
        )
        if response.status_code in _STATE_STORE_WRITE_OK:
            return True
        # Anything else is not a write we can vouch for. This used to be
        # `if status not in (200, 201, 204, 405): raise_for_status(); return True`,
        # which reported success for every status raise_for_status does not raise
        # on — and it only raises for 4xx/5xx. httpx does not follow redirects by
        # default, so a 301/302/307/308 (a STATE_STORE_URL missing its trailing
        # slash, an http->https upgrade) returned True for a write that never
        # happened, and short-circuited the local-file fallback in
        # save_seen_articles. State was then persisted nowhere.
        if response.status_code != _STATE_STORE_PUT_METHOD_REJECTED:
            SafeLogger.error(
                "state_store_put_failed",
                "Remote state PUT did not store the value",
                status_code=response.status_code,
                state_key=key,
            )
    except Exception as e:
        # ERROR, not WARN: state-persistence failures are the case AGENTS.md
        # principle 4 names explicitly. Matches the v4.19 WARN->ERROR promotion
        # already applied to _save_gist_state.
        SafeLogger.error(
            "state_store_put_failed",
            "Remote state PUT failed",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
            state_key=key,
        )

    try:
        response = httpx.post(
            endpoint,
            headers={**_state_store_headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=STATE_STORE_TIMEOUT_SECONDS
        )
        if response.status_code in _STATE_STORE_WRITE_OK:
            return True
        SafeLogger.error(
            "state_store_post_failed",
            "Remote state POST did not store the value",
            status_code=response.status_code,
            state_key=key,
        )
        return False
    except Exception as e:
        SafeLogger.error(
            "state_store_post_failed",
            "Remote state POST failed",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
            state_key=key,
        )
        return False
# How the Gist tier answered a read. The bool that `_load_gist_state_strict`
# returns is `status == GIST_READ_OK`, which is the right question for a writer
# — "may I overwrite what is there?" — and too coarse for a reader asking "is
# what I just wrote the thing a later read will get?".
#
# UNREACHABLE is the absence of evidence: transport, HTTP or auth failure, so
# the stored content is unknown and quite possibly exactly what we wrote.
# CORRUPT is evidence: the Gist answered, and what it holds cannot be parsed —
# so reads fall through PAST it to older tiers, and anything written to the Gist
# is invisible for as long as that lasts. Collapsing the two is what let a
# reservation broadcast on a Gist whose content no read can use (Codex review,
# 2026-10-19).
GIST_READ_OK = "ok"
GIST_READ_UNREACHABLE = "unreachable"
GIST_READ_CORRUPT = "corrupt"


def _load_gist_state_checked(filename: str) -> Tuple[Optional[Any], str]:
    """``_load_gist_state_strict``, reporting HOW the Gist tier answered.

    Returns ``(value, status)`` with status one of ``GIST_READ_OK`` (parsed
    content, a genuinely absent file, or no Gist configured),
    ``GIST_READ_UNREACHABLE`` (the request failed) or ``GIST_READ_CORRUPT``
    (the request succeeded and the content could not be parsed).
    """
    gist_id = os.environ.get("GIST_ID", "").strip()
    if not gist_id:
        return None, GIST_READ_OK  # local dev, no Gist configured — empty is legitimate
    token = os.environ.get("GIST_TOKEN", "").strip()
    headers: Dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.get(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers,
            timeout=STATE_STORE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        # Transport / HTTP / auth failure — the Gist itself was unreachable.
        # This is the untrusted case: state may well exist, we just couldn't
        # read it. Do NOT let callers treat this as "empty".
        SafeLogger.warn(
            "gist_state_read_failed",
            "Gist state read failed",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
            state_file=filename,
        )
        return None, GIST_READ_UNREACHABLE
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict) or filename not in files:
        # Gist reachable, but this file has never been written. Genuinely
        # absent — trustworthy empty (e.g. the first proactive run).
        return None, GIST_READ_OK
    try:
        return json.loads(files[filename]["content"]), GIST_READ_OK
    except Exception as e:
        # Stored content is corrupt/unparseable. Treat as untrusted rather
        # than silently clobbering it with an empty state.
        SafeLogger.warn(
            "gist_state_parse_failed",
            "Gist state content could not be parsed",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
            state_file=filename,
        )
        return None, GIST_READ_CORRUPT


def _load_gist_state_strict(filename: str) -> Tuple[Optional[Any], bool]:
    """Load a JSON state file from a private Gist, distinguishing a
    TRUSTWORTHY empty from an UNTRUSTED one.

    Returns ``(value, trusted)``:
      - ``trusted=True``  — the result is safe to act on. Either the file
        loaded fine (value = parsed JSON), or the read genuinely found no
        such state (value = None) because no Gist is configured (local dev)
        or the Gist is reachable but the file has never been written.
      - ``trusted=False`` — the read itself failed (transport / HTTP / auth /
        corrupt-content). value = None, but that None means "could not read",
        NOT "no state exists". Callers must NOT overwrite real state on this.

    Why this matters: ``_load_gist_state`` collapses every failure to ``None``,
    so a transient Gist read failure is indistinguishable from genuinely-absent
    state. For append-only / approval state (``pending_replies.json``) that
    ambiguity is destructive — a save after a failed read overwrites real
    pending/posted/rejected history. This strict variant lets those callers
    skip work instead. (Flagged by the Codex review on 2026-06-12; see
    AGENTS.md "fail loud on critical state".)

    Thin wrapper over ``_load_gist_state_checked``. It folds UNREACHABLE and
    CORRUPT into one ``False``, which is correct for every caller asking "may I
    overwrite what is there?" and too coarse for one asking "will a later read
    see what I just wrote?" — that one wants the status.
    """
    value, status = _load_gist_state_checked(filename)
    return value, status == GIST_READ_OK
def _load_gist_state(filename: str) -> Optional[Any]:
    """Load a JSON state file from a private GitHub Gist.

    Thin wrapper over ``_load_gist_state_strict`` that drops the trust flag —
    preserves the original "None on any failure or absence" contract for
    callers (seen_articles, etc.) that have their own multi-tier fallbacks.
    State that must NOT be overwritten on a failed read should call
    ``_load_gist_state_strict`` directly and honour ``trusted``.
    """
    return _load_gist_state_strict(filename)[0]
def _save_gist_state(filename: str, data: Any) -> bool:
    """Save a JSON state file to a private GitHub Gist."""
    gist_id = os.environ.get("GIST_ID", "").strip()
    if not gist_id:
        return False
    token = os.environ.get("GIST_TOKEN", "").strip()
    headers: Dict[str, str] = {"Accept": "application/vnd.github+json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers=headers,
            json={"files": {filename: {"content": json.dumps(data, indent=2)}}},
            timeout=STATE_STORE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        # v4.19 (2026-05-13): promoted WARN → ERROR per the 2026-04-22 retro
        # callback. Silent state-persistence failures are worse than noisy ones.
        # The bot continues (returns False, caller falls back to local file),
        # but the run's log now shows ERROR-level events surfaced in the
        # GitHub Actions UI — same visibility as broadcast_invariant_violated
        # and content_generation_exhausted. The retro's other mitigation
        # ("surface count in weekly digest") still applies once Phase 2 lands;
        # this is the cheap defensible interim.
        SafeLogger.error(
            "gist_state_save_failed",
            "Gist state save failed",
            error_type=type(e).__name__,
            error_msg=str(e)[:200],
            state_file=filename,
        )
        return False
def _atomic_write_json(file_path: Path, data: Any) -> None:
    """Atomically write JSON by replacing target with a temp file in same dir."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path_str = tempfile.mkstemp(prefix=f".{file_path.name}.", suffix=".tmp", dir=file_path.parent)
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, file_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
def _restore_from_backup(file_path: Path, backup_path: Path) -> Optional[Any]:
    """Load ``backup_path`` and write it back over ``file_path``; None if unusable."""
    try:
        with open(backup_path, "r") as backup_file:
            restored_data = json.load(backup_file)
        _atomic_write_json(file_path, restored_data)
        SafeLogger.warn("json_restored_from_backup", "Restored file from backup", file_path=str(file_path), backup_path=str(backup_path))
        return restored_data
    except Exception as backup_error:
        SafeLogger.error("json_backup_restore_failed", "Backup restore failed", exception=backup_error, file_path=str(file_path))
        return None


def _load_json_with_repair_strict(
    file_path: Path,
    default_factory: Callable[[], T],
    *,
    migrate_list_to_seen_shape: bool = False
) -> Tuple[T, bool]:
    """Load JSON with corruption repair, and say whether the result came from disk.

    - If the file is missing but its ``.bak`` survives, restore from the backup:
      a backup without its primary is an interrupted or half-cleaned write, not
      an empty history.
    - If decode fails, try restoring from ``.bak``.
    - If the backup is also invalid or missing, preserve the corrupt file and
      reset to default.

    Returns ``(data, from_file)``. ``from_file`` is False whenever ``data`` is the
    synthesised default, so a caller can tell "the local tier had nothing" from
    "the local tier had real state". The load chains used to decide that from
    whether a file EXISTED, and a stray ``.bak`` then made an empty default look
    like real local state (found 2026-09-11).
    """
    default_data = default_factory()
    backup_path = file_path.with_suffix(file_path.suffix + ".bak")
    if not file_path.exists():
        restored_data = _restore_from_backup(file_path, backup_path) if backup_path.exists() else None
        if restored_data is None:
            return default_data, False
        data = restored_data
    else:
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            SafeLogger.error("json_corrupt_detected", "Corrupt JSON detected", exception=e, file_path=str(file_path))
            restored_data = _restore_from_backup(file_path, backup_path) if backup_path.exists() else None
            if restored_data is None:
                import time as _time
                corrupt_copy = file_path.parent / f"{file_path.name}.corrupt.{int(_time.time())}"
                moved = False
                try:
                    os.replace(file_path, corrupt_copy)
                    moved = True
                    SafeLogger.warn("json_corrupt_moved", "Moved corrupt file", file_path=str(file_path), corrupt_copy=str(corrupt_copy))
                except Exception as move_error:
                    SafeLogger.error("json_corrupt_move_failed", "Failed moving corrupt file", exception=move_error, file_path=str(file_path))
                if moved:
                    _atomic_write_json(file_path, default_data)
                return default_data, False
            data = restored_data
        except Exception as e:
            SafeLogger.error("json_load_failed", "Failed to load JSON", exception=e, file_path=str(file_path))
            return default_data, False

    if migrate_list_to_seen_shape and isinstance(data, list):
        migrated = {"links": data, "recent_topics": [], "pioneer_recent": []}
        _atomic_write_json(file_path, migrated)
        return migrated, True  # type: ignore[return-value]

    return data, True


def _load_json_with_repair(
    file_path: Path,
    default_factory: Callable[[], T],
    *,
    migrate_list_to_seen_shape: bool = False
) -> T:
    """``_load_json_with_repair_strict`` without the from-file flag, for callers
    that do not need to know where the value came from (the metrics files)."""
    return _load_json_with_repair_strict(
        file_path, default_factory, migrate_list_to_seen_shape=migrate_list_to_seen_shape
    )[0]
def prune_pioneer_recent(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop pioneer_recent entries older than PIONEER_COOLDOWN_DAYS.

    Tolerant of malformed entries (missing keys, unparseable dates) — they're
    dropped silently. Idempotent.
    """
    if not entries:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=PIONEER_COOLDOWN_DAYS)
    kept = []
    for entry in entries:
        if not isinstance(entry, dict) or "id" not in entry or "posted_at" not in entry:
            continue
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            continue
        if posted_at >= cutoff:
            kept.append(entry)
    return kept
def _file_lock(lock_path: Path):
    """Context manager for an advisory process lock."""
    return file_lock(lock_path)
def _ensure_pioneer_field(data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure pioneer_recent key exists. In-place add for old state shapes."""
    if "pioneer_recent" not in data:
        data["pioneer_recent"] = []
    return data
def load_seen_articles_strict() -> Tuple[Dict[str, Any], bool]:
    """``load_seen_articles``, plus whether the result is safe to write back.

    ``trusted=False`` means the Gist read itself failed AND no other tier supplied
    real state. The empty default that comes back then means "could not read", not
    "no state exists" — writing it back erases every seen link, recent topic and
    Pioneer cooldown the bot has accumulated.

    That is not hypothetical. ``seen_articles.json`` is gitignored, so a fresh
    GitHub Actions runner has no local copy, and ``STATE_STORE_URL`` is usually
    unset. One transient Gist GET failure therefore walks the whole chain to the
    empty default. This is the same distinction ``_load_gist_state_strict`` draws
    for ``pending_replies.json`` (Codex review, 2026-06-12); it was never extended
    to the seen state, which is the larger loss of the two.
    """
    state, trusted, _ = load_seen_articles_sourced()
    return state, trusted


def load_seen_articles_sourced() -> Tuple[Dict[str, Any], bool, str]:
    """``load_seen_articles_strict``, plus HOW THE GIST TIER answered.

    Returns ``(state, trusted, gist_status)``, the status being one of
    ``GIST_READ_OK`` / ``GIST_READ_UNREACHABLE`` / ``GIST_READ_CORRUPT``.

    ``trusted`` is an aggregate: it says some tier supplied real state, so the
    value is safe to write back. That is the right question for a writer and the
    WRONG one for anything asking "will the next run see what I just wrote",
    because this chain reads Gist -> store -> local and ``trusted`` is True the
    moment the STORE answers, even when the Gist GET failed on the way past.

    ``gist_status`` is the missing piece. ``GIST_READ_OK`` means the Gist tier
    gave a definite answer — its content, "no such file", or "no Gist
    configured" — and only then can anything read from a LOWER tier be relied on
    to survive, because the Gist answers FIRST and its content shadows whatever
    the store or the local file reported. The other two statuses are both
    "cannot rely on it", for opposite reasons: UNREACHABLE means the Gist's
    content is unknown, CORRUPT means it is known and unusable.

    That distinction is what the pre-broadcast reservation verifies against: a
    claim written to the store while the Gist is merely unreadable reads back
    perfectly from the store and vanishes the moment the Gist comes back, which
    is the duplicate-publication window the reservation exists to close (Codex
    review, 2026-10-19).
    """
    default: Dict[str, Any] = {"links": [], "recent_topics": [], "pioneer_recent": []}
    # 1. Try Gist
    gist_data, gist_status = _load_gist_state_checked("seen_articles.json")
    if isinstance(gist_data, dict) and "links" in gist_data and "recent_topics" in gist_data:
        return _ensure_pioneer_field(gist_data), True, GIST_READ_OK
    gist_trusted = gist_status == GIST_READ_OK
    # 2. Existing STATE_STORE_URL fallback
    remote_data = _load_state_from_store("seen_articles")
    if isinstance(remote_data, dict) and "links" in remote_data and "recent_topics" in remote_data:
        return _ensure_pioneer_field(remote_data), True, gist_status
    if isinstance(remote_data, list):
        migrated = {"links": remote_data, "recent_topics": [], "pioneer_recent": []}
        _save_state_to_store("seen_articles", migrated)
        return migrated, True, gist_status
    # 3. Local file fallback
    data, from_file = _load_json_with_repair_strict(
        SEEN_FILE,
        lambda: default,
        migrate_list_to_seen_shape=True
    )
    if isinstance(data, dict) and "links" in data and "recent_topics" in data:
        # The synthesised default is indistinguishable from genuinely-empty
        # state. Real local state (read from the file or its backup) is
        # trustworthy on its own; the default is only trustworthy if the Gist
        # read ahead of it actually succeeded. Deciding this from whether a file
        # exists let a stray .bak mark the default as real (fixed 2026-09-11).
        return _ensure_pioneer_field(data), (from_file or gist_trusted), gist_status
    # 4. Nothing supplied real state. Whether the empty default is trustworthy
    #    depends entirely on whether the Gist read succeeded: a reachable Gist
    #    with no file yet is a legitimate first run, an unreachable one is not.
    if not gist_trusted:
        SafeLogger.error(
            "seen_articles_read_untrusted",
            "Seen-state read failed and no fallback tier had state; treating empty as UNKNOWN",
            gist_status=gist_status,
        )
        return default, False, gist_status
    SafeLogger.warn("seen_articles_format_repaired", "Unexpected seen_articles format detected; repairing to default shape")
    _atomic_write_json(SEEN_FILE, default)
    return default, True, GIST_READ_OK


def load_seen_articles() -> Dict[str, Any]:
    """Load seen state including links, recent topics, and pioneer cooldown.

    Thin wrapper over ``load_seen_articles_strict`` that drops the trust flag,
    preserving the original contract for read-only callers. Anything that will
    WRITE the result back must use the strict variant and honour ``trusted``.
    """
    return load_seen_articles_strict()[0]


def _remote_state_configured() -> bool:
    """Is either remote state tier configured at all?

    The one thing a read-back cannot discover: on a GitHub Actions runner the
    local tier is reached only when both remote tiers refused the write, and that
    file is thrown away with the job — so the claim reads back fine in THIS
    process and is gone by the next run. In local development the local file is
    the only tier there is, and a reservation on it is perfectly real.
    """
    return bool(
        os.environ.get("GIST_ID", "").strip()
        or os.environ.get("STATE_STORE_URL", "").strip()
    )


def save_seen_articles(seen_data: Dict[str, Any]) -> Optional[str]:
    """Persist the seen state, returning WHICH tier accepted the write.

    ``"gist"`` / ``"store"`` / ``"local"``, or None when the value is stored
    nowhere. Callers that only need the side effect can ignore the return, as
    every caller did before the pre-broadcast reservation arrived: that one has
    to abort the broadcast when its claim did not land, so "it was written" is
    no longer a question the function can decline to answer.
    """
    if _save_gist_state("seen_articles.json", seen_data):
        return "gist"
    if _save_state_to_store("seen_articles", seen_data):
        return "store"
    try:
        _atomic_write_json(SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".bak"), seen_data)
        _atomic_write_json(SEEN_FILE, seen_data)
        return "local"
    except Exception as e:
        SafeLogger.error("seen_articles_save_failed", "Failed to save seen articles", exception=e)
        return None
def load_replied_to_strict() -> Tuple[List[str], bool]:
    """Load replied_to state, plus whether the result is safe to write back.

    The same distinction ``load_seen_articles_strict`` draws, for the same reason:
    ``replied_to.json`` is gitignored, so a fresh Actions runner has no local copy
    and ``STATE_STORE_URL`` is usually unset. One transient Gist GET failure walks
    the chain to an empty list, which is indistinguishable from "nobody has been
    replied to yet" — and writing it back forgets every mention already answered.

    The cost is not silent here. ``handle_interactions`` skips a mention that is
    already in this list, so an erased list means the bot replies a second time to
    people it has already answered, on their timeline.
    """
    # 1. Try Gist
    gist_data, gist_trusted = _load_gist_state_strict("replied_to.json")
    if isinstance(gist_data, list):
        return gist_data, True
    # 2. Existing STATE_STORE_URL fallback
    remote_data = _load_state_from_store("replied_to")
    if isinstance(remote_data, list):
        return remote_data, True
    # 3. Local file fallback
    loaded: Tuple[List[str], bool] = _load_json_with_repair_strict(REPLIED_FILE, lambda: [])
    data, from_file = loaded
    if isinstance(data, list):
        # The synthesised empty list is indistinguishable from a genuinely empty
        # history. Real local state (read from the file or its backup) is
        # trustworthy on its own; the empty default is only trustworthy if the
        # Gist read ahead of it actually succeeded (see load_seen_articles_strict).
        return data, (from_file or gist_trusted)
    if not gist_trusted:
        SafeLogger.error(
            "replied_to_read_untrusted",
            "Replied-to read failed and no fallback tier had state; treating empty as UNKNOWN",
        )
        return [], False
    SafeLogger.warn("replied_to_format_repaired", "Unexpected replied_to format detected; repairing to default shape")
    _atomic_write_json(REPLIED_FILE, [])
    return [], True


def save_replied_to(replied_ids: List[str]) -> None:
    if _save_gist_state("replied_to.json", replied_ids):
        return
    if _save_state_to_store("replied_to", replied_ids):
        return
    try:
        _atomic_write_json(REPLIED_FILE.with_suffix(REPLIED_FILE.suffix + ".bak"), replied_ids)
        _atomic_write_json(REPLIED_FILE, replied_ids)
    except Exception as e:
        SafeLogger.error("replied_to_save_failed", "Failed to save replied state", exception=e)
def update_seen_articles_checked(
    mutator: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    require_gist_read: bool = False,
    corrupt_gist_is_inert: bool = False,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Lock-protected read-modify-write for seen_articles.json.

    Skips the write entirely when the read could not be trusted. An untrusted
    read yields the empty default, and saving that would replace the real state
    with nothing — one transient Gist failure costing every seen link, recent
    topic and Pioneer cooldown. Missing one run's bookkeeping is the cheaper
    failure, the same trade AGENTS.md principle 2 makes for content.

    The mutator is handed the state just read under the lock. Callers must apply
    their changes to THAT value rather than returning a snapshot captured earlier
    in the run — a stale snapshot defeats both the lock and this guard.

    Returns ``(state, tier)``, where ``tier`` is the storage tier that accepted
    the write (see ``save_seen_articles``) and None means the mutation is stored
    nowhere — either because the read was untrusted and the write was skipped, or
    because every tier refused it. ``update_seen_articles`` drops the tier for
    callers that do not need it.

    ``require_gist_read`` additionally refuses to write when the GIST TIER did
    not answer the read, which ``trusted`` alone does not cover: a failed Gist
    GET with a healthy store is still "trusted", because real state came back.
    What came back is then state the Gist cannot see into, and the write that
    follows PATCHes it straight over the Gist — so a decision the mutator made
    against it (is this URL already claimed? already in ``links``?) was made
    blind, and the write destroys the very entries that would have said no. For
    the reservation that is the duplicate-publication window reopened: another
    run's claim is erased, replaced with ours, and the story goes out twice
    (Codex review, 2026-10-19).

    It is opt-in because it is only worth a missed write where the mutator's
    decision depends on state the Gist might hold alone. The three reservation
    entry points pass it; bookkeeping callers do not. With no Gist configured
    the status is OK, so local development is unaffected.

    ``corrupt_gist_is_inert`` narrows that refusal back for the one status whose
    premise is false. The guard refuses because the write would PATCH over
    entries the Gist alone holds and the mutator could not see — but for CORRUPT
    content nothing READS those entries either: ``load_seen_articles_sourced``
    falls straight past unparseable Gist content to the store, so the state the
    mutator decided against is exactly the state every reader is already getting,
    and the bytes about to be overwritten are suppressing nothing. UNREACHABLE
    keeps the refusal, and that asymmetry is the point: there the content is
    unknown rather than known to be unusable, and it answers reads again the
    moment it is back.

    Pass it only from a caller whose write SUPPRESSES something. It trades away
    any chance of repairing that Gist by hand, which is worth it exactly where
    withholding the write would leave a story selectable after a platform may
    have taken it — see ``settle_broadcast`` (#201).

    It narrows the GIST guard and nothing else. An untrusted read still refuses,
    and that is not a corner case: ``daily_post.yml`` configures ``GIST_ID`` and
    no ``STATE_STORE_URL``, and ``seen_articles.json`` is gitignored, so on a
    runner the Gist is the ONLY tier that can supply state. A corrupt Gist there
    leaves nothing to derive a write from, and the empty default would replace
    the history rather than extend it — so the suppressing write is refused in
    production's own configuration, loudly, and the thing that prevents the
    repost is that ``reserve_broadcast`` needs this same read and so nothing
    posts at all (Codex review on #218).
    """
    lock_path = SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".lock")
    with _file_lock(lock_path):
        current, trusted, gist_status = load_seen_articles_sourced()
        if not trusted:
            if corrupt_gist_is_inert:
                # The caller is a SUPPRESSING write, and it is being refused
                # for a reason the flag below cannot narrow: no tier supplied
                # state at all, so there is nothing to derive a write FROM.
                # Writing anyway would put the empty default in the Gist —
                # every seen link, recent topic and Pioneer cooldown replaced
                # with nothing, and a parseable Gist that lets the next run
                # resume posting at once with no memory. That turns one
                # possibly-live story into every story still inside the feed
                # window, so the refusal stands. What holds the line instead is
                # that `reserve_broadcast` needs this same read, so nothing
                # posts until the tier is readable (AGENTS.md principle 2: a
                # missed run beats a duplicate). Said out loud, because the
                # generic line below reads like routine bookkeeping and this is
                # a story that may be live and is recorded nowhere
                # (Codex review on #218).
                SafeLogger.error(
                    "seen_articles_suppressing_write_unstorable",
                    "No tier supplied state, so a story that may already be live cannot be recorded; runs stay blocked until the state tier reads",
                    gist_status=gist_status,
                )
            SafeLogger.error(
                "seen_articles_update_skipped",
                "Seen-state read was untrusted; skipping the write rather than erasing history",
                gist_status=gist_status,
            )
            return current, None
        if require_gist_read and gist_status != GIST_READ_OK:
            if gist_status == GIST_READ_CORRUPT and corrupt_gist_is_inert:
                # Loud, although the run then proceeds correctly: the Gist
                # holding content no read can parse is a critical-state failure,
                # and this write replaces it with state derived from a lower
                # tier, so whatever it held is gone for good. A WARN here would
                # hide both facts behind a line about a write that worked
                # (AGENTS.md principle 4).
                SafeLogger.error(
                    "seen_articles_update_over_corrupt_gist",
                    "Gist content is unparseable, so it suppresses nothing; writing the suppressing update over it",
                    gist_status=gist_status,
                )
            else:
                SafeLogger.error(
                    "seen_articles_update_skipped_gist_unread",
                    "Gist did not answer the read, so this write would decide blind and overwrite it; skipping",
                    gist_status=gist_status,
                )
                return current, None
        updated = mutator(current)
        tier = save_seen_articles(updated)
    return updated, tier


def update_seen_articles(mutator: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
    """``update_seen_articles_checked`` without the storage-tier flag, for the
    callers whose behaviour does not depend on whether the write landed."""
    return update_seen_articles_checked(mutator)[0]


# --- Pre-broadcast reservations ---------------------------------------------
#
# `persistence_stage` records a published story's dedup entry AFTER delivery, so
# a run that posted and then died — a cancelled workflow, an evicted runner, a
# hang in one of the stages that follow the post — left the post live and
# unrecorded, and the next run twelve hours later could select and post the same
# story again. The window was narrow (the `links` cap plus the two-day
# feed-fetch window) and was never observed firing, but it was real.
#
# A reservation closes it by reversing the order: claim the URL first, broadcast
# second, settle third. The claim lives in the same `seen_articles` state as
# `links`, in `pending_broadcast`, and is read as already-seen at selection time
# (`reserved_links`), so an unsettled claim suppresses the story exactly as a
# `links` entry would.
#
# Two stages, and the difference between them is diagnostic rather than
# behavioural — both suppress, neither is pruned by age:
#
#   "pending"   — written before the broadcast. A run that settles overwrites or
#                 removes it, so finding one means the run never got that far:
#                 the post may well be live. Suppressing it is the whole point.
#   "uncertain" — written when a platform TRIED and no id came back. Not the same
#                 as "nothing was posted": a send that lands and then loses its
#                 response looks identical from here, which is why the claim is
#                 kept rather than cleared.
#
# Keeping an uncertain claim costs one candidate story. Clearing it would reopen
# the window this exists to close, and a repost is the failure this project
# cannot take back; the suppression is also an order of magnitude smaller than
# the one deliberately removed from `persistence_stage` (four candidates on
# every SUCCESSFUL run, against one on a total-failure run).
#
# A run where no platform even tried is the third outcome, and it keeps no claim
# at all. "Uncertain" is justified only by the ambiguity of a send whose response
# was lost; where there was no send — Mastodon switched off, Bluesky with no
# session, content the length invariant refused — there is no ambiguity to
# protect against, and holding the claim would suppress a story for nothing,
# indefinitely. That is why `attempted` exists on BroadcastResult rather than
# being inferred from an empty id list (Codex review, 2026-10-19).
#
# KNOWN AND DELIBERATELY NOT FIXED: claims are never pruned by age. Unlike
# `links`, which is capped at SEEN_LINKS_WINDOW and rotates, a claim only leaves
# `pending_broadcast` when a run settles it or the MAX_BROADCAST_RESERVATIONS cap
# pushes it out.
#
# A stranded claim is not free. It suppresses its URL on every Curator run until
# that entry's PUBLISHED date crosses `fetch_single_feed`'s two-day cutoff
# (src/news.py) — at two scheduled runs a day, as many as four of them. That is
# the same order of cost as keeping an `uncertain` claim, and it is the price of
# the suppression being unconditional. What ages out is the claim's EFFECT, not
# the claim: once the entry leaves the window the record sits in the state with
# nothing left to suppress.
#
# That inert tail is what pruning would clean up, and it is also why the one
# vector worth naming is NOT the tail: a feed that re-issues the SAME canonical
# URL with a fresh date — an updated article, a feed that bumps
# `updated_parsed` — puts the story back inside the two-day window, where the
# long-inert claim blocks it again. Real, and marginal.
#
# Do not "fix" it with age-based pruning, which cannot be both safe and useful.
# A claim suppresses a story only while that story is still selectable, so a TTL
# SHORTER than the two-day window drops claims that are still doing their job —
# that is the repost window, reopened, and reopened precisely in the case the
# claim exists for (a run that posted and died, where the post is by definition
# recent). A TTL LONGER than the window drops only claims that could no longer
# suppress anything, which is tidiness, not a fix. And neither setting touches
# the vector above: a re-issued URL arrives with a FRESH date against an OLD
# claim, so the TTL that would clear it is bounded by the gap between the first
# publication and the re-issue — a feed-side number this code cannot know
# (assessment, 2026-10-19).
RESERVATION_STAGE_PENDING = "pending"
RESERVATION_STAGE_UNCERTAIN = "uncertain"

# The one storage tier whose accepted write nothing can shadow, and so the one
# that may be trusted WITHOUT a read-back. `_save_gist_state` is the first step
# of `save_seen_articles` and `_load_gist_state_strict` is the first step of
# `load_seen_articles_strict`, so a claim the Gist accepted is the claim the next
# read returns. Every other tier is reached only because a higher one refused the
# write, and a higher tier that later answers reads hides what the lower one
# holds. This must be kept in step with the order of those two functions; it is
# the only tier inference in this module, and it is a fact about them rather than
# about the environment.
_UNSHADOWABLE_TIER = "gist"


def reserved_links(seen_data: Dict[str, Any]) -> List[str]:
    """Canonical URLs held by a live reservation, for the seen-link filter.

    Reads `pending_broadcast` defensively: a state file written before this field
    existed simply has no reservations, which needs no migration step, and a
    malformed entry is skipped rather than crashing selection.
    """
    links: List[str] = []
    for entry in seen_data.get("pending_broadcast") or []:
        if not isinstance(entry, dict):
            continue
        link = entry.get("link")
        if isinstance(link, str) and link:
            links.append(link)
    return links


def _prune_reservations(
    entries: Any, *, drop_link: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Well-formed reservation entries, optionally minus one link."""
    kept: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("link"):
            continue
        if drop_link is not None and entry.get("link") == drop_link:
            continue
        kept.append(entry)
    return kept


def _release_claim(canonical: str, *, reason: str) -> None:
    """Drop a claim for a story that will definitely not be broadcast.

    Called when the reservation is refused AFTER its write landed. The caller
    aborts the broadcast, so nothing can have been posted — and because claims
    are never pruned by age, leaving this one ``pending`` would suppress the
    story on every future run. A transient read-back failure would silently cost
    an article forever, which is the same over-suppression the ``attempted``
    distinction removed from settlement (Codex review, 2026-10-19).

    Best-effort, and loud when it fails: if this write is refused too, the claim
    stays and that story is out of the pool until the reservation cap pushes it
    out. That happens when this read fails for the same reason the caller's
    verification did — both remote GETs down while the store accepted the write
    — and it is deliberately NOT repaired by remembering the pre-claim state and
    writing it straight back to the tier that took it. Doing so would push a
    snapshot taken before the lock-protected read back over whatever that tier
    holds now, which is the stale-snapshot overwrite
    ``update_seen_articles_checked`` documents as the thing callers must not do:
    a bigger hole than the orphaned claim, traded for a narrower one. One
    candidate story, bounded by the feed window and logged at ERROR, is the
    cheaper failure (Codex review, 2026-10-19).

    Unlike the release in ``settle_broadcast``, this one needs no read-back of
    its own. Both callers reach it only AFTER establishing, on a read they could
    trust, that the claim is not visible through the read chain — one because the
    read-back found nothing, the other because the write reached only a local
    file that reads do not consult — so the story is not being suppressed either
    way, and this is tidying up a write that may not be observable in the first
    place. Neither caller reaches it on a read that merely FAILED: that used to
    land here, where the strict read inside ``update_seen_articles_checked``
    skipped the write and left the claim standing anyway.
    """
    def _apply(current: Dict[str, Any]) -> Dict[str, Any]:
        current["pending_broadcast"] = _prune_reservations(
            current.get("pending_broadcast"), drop_link=canonical
        )
        return current

    _, tier = update_seen_articles_checked(_apply, require_gist_read=True)
    if tier is None:
        SafeLogger.error(
            "broadcast_claim_release_failed",
            "Could not release the claim on a story that was never broadcast",
            reserved_link=canonical,
            release_reason=reason,
        )
        return
    SafeLogger.info(
        "broadcast_claim_released",
        "Released the claim on a story that was never broadcast",
        reserved_link=canonical,
        release_reason=reason,
    )


def reserve_broadcast(link: str) -> bool:
    """Claim ``link`` before it is broadcast. True only if the claim is ours.

    False means the caller must NOT broadcast: an unstored claim is the hole this
    exists to close, so posting anyway would publish a story with no record that
    it was published. Logged at ERROR per AGENTS.md principle 4 — this is
    critical state, not best-effort bookkeeping.

    False is reserved for a claim this run was REFUSED or could not store. An
    unreadable state is neither, and returns True — see the read-back below.

    Acquisition is a claim on an UNCLAIMED, UNSEEN story. A URL that the state
    read under the lock already carries — as a reservation or as a ``links``
    entry — is a failed acquisition, not a retry: there is no owner token that
    could prove the existing claim belongs to this run, so the only safe reading
    is that it belongs to another one. This matters because selection runs on the
    non-strict read: one transient Gist failure hands ``content_prep_stage`` the
    empty default, which can reselect a story a crashed run already claimed, and
    an earlier version of this function then deleted that claim and replaced it
    with its own — verification passed, and the story was reposted. The hole this
    whole mechanism exists to close, reachable through the mechanism itself
    (Codex review, 2026-10-19).
    """
    canonical = canonical_url(link)
    conflict: Dict[str, Optional[str]] = {"reason": None}

    def _apply(current: Dict[str, Any]) -> Dict[str, Any]:
        if canonical in (current.get("links") or []):
            conflict["reason"] = "already_seen"
            return current
        entries = _prune_reservations(current.get("pending_broadcast"))
        if any(entry.get("link") == canonical for entry in entries):
            conflict["reason"] = "already_claimed"
            return current
        entries.append({
            "link": canonical,
            "stage": RESERVATION_STAGE_PENDING,
            "reserved_at": datetime.now(timezone.utc).isoformat(),
        })
        current["pending_broadcast"] = entries[-MAX_BROADCAST_RESERVATIONS:]
        return current

    _, tier = update_seen_articles_checked(_apply, require_gist_read=True)
    # Checked before the storage outcome, and never followed by a release: the
    # claim in the way is someone else's and must survive this run.
    if conflict["reason"] is not None:
        SafeLogger.error(
            "broadcast_reservation_conflict",
            "Story was already claimed or already seen in the state read under the lock",
            reserved_link=canonical,
            conflict=conflict["reason"],
        )
        return False
    if tier is None:
        SafeLogger.error(
            "broadcast_reservation_failed",
            "Could not store the pre-broadcast reservation",
            reserved_link=canonical,
        )
        return False
    if tier == "local" and _remote_state_configured():
        SafeLogger.error(
            "broadcast_reservation_not_durable",
            "Reservation reached only a local file that will not survive this job",
            reserved_link=canonical,
            storage_tier=tier,
        )
        _release_claim(canonical, reason="not_durable")
        return False
    # Read the claim back through the very chain the next run will use, rather
    # than reasoning about which tier ought to be authoritative. Two rounds of
    # that reasoning were wrong in opposite directions: the first accepted a
    # write to the fallback store that a readable Gist would shadow, and the
    # second rejected a write to that same store in the configuration where the
    # Gist is reachable but holds no seen_articles.json and the read genuinely
    # does fall through to it (Codex review, 2026-10-19). The question is only
    # ever "will the next run see this claim?", and a read answers it directly —
    # immune to any later change in the read precedence, which inference is not.
    #
    # Two things have to hold, and the aggregate `trusted` flag answers only the
    # second of them:
    #
    #   1. The GIST TIER settled. `trusted` goes True the moment the store
    #      answers, even when the Gist GET failed on the way past — so a claim
    #      written to the store while the Gist is merely unreadable reads back
    #      perfectly, passes verification, and then vanishes the moment the Gist
    #      recovers and its older file answers first. That is the duplicate
    #      window this mechanism exists to close, reached THROUGH the read-back
    #      meant to close it (Codex review, 2026-10-19). `gist_settled` is the
    #      flag that distinguishes "the Gist has no state" from "the Gist did not
    #      answer", and it is False in strictly more cases than `trusted` is.
    #   2. The claim is in what came back.
    #
    # Checking a read rather than inferring from config is still the point: two
    # rounds of that inference were wrong in opposite directions, accepting a
    # store write a readable Gist would shadow and then rejecting one in the
    # configuration where the Gist is reachable, holds no seen_articles.json and
    # the read genuinely does fall through (Codex review, 2026-10-19). A settled
    # Gist that simply has no file still verifies a store claim, because the next
    # run's read falls through exactly the same way.
    seen_after, _read_trusted, gist_status = load_seen_articles_sourced()
    if gist_status == GIST_READ_CORRUPT:
        # Not "I could not check" — a check that came back with an answer, and
        # the answer is that the Gist holds content no read can parse. Every
        # read falls PAST it to older store or local state, which has no claim
        # in it, so the story is selectable again however well the PATCH went.
        # A Gist-tier write does not save this: `_save_gist_state` writes valid
        # JSON, so seeing corruption AFTER our own successful PATCH means
        # something overwrote it or we are being served something other than
        # what we wrote — either way, not our claim. Positive evidence of
        # invisibility is a refusal, not an unverified success (Codex review,
        # 2026-10-19).
        SafeLogger.error(
            "broadcast_reservation_gist_unparseable",
            "Gist content cannot be parsed, so reads fall past the claim; refusing rather than broadcasting",
            reserved_link=canonical,
            storage_tier=tier,
            gist_status=gist_status,
        )
        _release_claim(canonical, reason="gist_unparseable")
        return False
    if gist_status != GIST_READ_OK:
        # Cannot verify, because the tier that answers first did not answer.
        # What may be concluded without that answer depends entirely on WHICH
        # tier took the write, and "any tier that is not None" is not good
        # enough (Codex review, 2026-10-19):
        #
        #   tier == "gist"  — the PATCH succeeded against the same Gist whose GET
        #                     failed. Nothing precedes it in either chain, so when
        #                     it answers again it answers with this claim.
        #   tier == "store" — the Gist PATCH FAILED and the fallback took the
        #                     write, while GIST_ID is still set. The moment the
        #                     Gist recovers, its older seen_articles.json is
        #                     returned and the store is never consulted: the claim
        #                     is invisible and the story can be reposted. Refused,
        #                     even though refusing costs the run — and may cost it
        #                     twice, since `_release_claim` can only clean up if
        #                     its own read succeeds. A possible duplicate is the
        #                     failure this project cannot take back; a missed run
        #                     is not.
        #   tier == "local" — already refused above when a remote tier is
        #                     configured, which an unsettled Gist guarantees.
        #
        # Why the unreachable case is transient rather than a Gist that has
        # been down all along: the write above ran under `require_gist_read`,
        # so it only happened because the Gist answered the locked read moments
        # earlier. An unreachable read-back therefore means the Gist went away
        # BETWEEN that read and this one.
        #
        # This used to be argued from "the write happened at all", which was
        # wrong: plain `update_seen_articles_checked` writes on the aggregate
        # trust flag, so a failed Gist GET with a healthy store still permits
        # one (Codex review, 2026-10-19). The guard is what makes the sentence
        # true; if it is ever removed, this reasoning goes with it.
        if tier != _UNSHADOWABLE_TIER:
            SafeLogger.error(
                "broadcast_reservation_shadowable",
                "Claim went to a fallback tier a recovering Gist would shadow, and the Gist did not answer the check",
                reserved_link=canonical,
                storage_tier=tier,
                gist_status=gist_status,
            )
            _release_claim(canonical, reason="shadowable_tier")
            return False
        # Cannot verify, but the tier that took the write is the tier reads ask
        # first, so the claim is almost certainly stored. Aborting on top of that
        # trades a near-certain success for a certain silent failure. Proceed,
        # and say in the log that the proof is missing.
        SafeLogger.warn(
            "broadcast_reservation_unverified",
            "Gist did not answer, so the reservation could not be read back; broadcasting on the Gist-tier write",
            reserved_link=canonical,
            storage_tier=tier,
            gist_status=gist_status,
        )
        return True
    if canonical not in reserved_links(seen_after):
        SafeLogger.error(
            "broadcast_reservation_not_readable",
            "Reservation did not read back through the normal state chain",
            reserved_link=canonical,
            storage_tier=tier,
        )
        _release_claim(canonical, reason="not_readable")
        return False
    SafeLogger.info(
        "broadcast_reserved",
        "Story reserved ahead of the broadcast",
        reserved_link=canonical,
        storage_tier=tier,
    )
    return True


def settle_broadcast(link: str, *, delivered: bool, attempted: bool = True) -> None:
    """Close out the reservation for ``link`` once the broadcast has returned.

    Three outcomes:

    - ``delivered=True`` — any platform returned an id, partial threads included.
      The claim becomes the ordinary ``links`` entry and is dropped.
    - ``delivered=False, attempted=True`` — a platform tried and nothing came
      back. The claim is marked ``uncertain`` and KEPT; see the note above this
      function's section for why that is not the same as clearing it.
    - ``delivered=False, attempted=False`` — nothing tried, so nothing can have
      been posted. The claim is released and the story is free to run next time.

    ``attempted`` defaults to True because that is the suppressing reading, and a
    caller that cannot tell should get the safe one.

    The first two outcomes need no readable-tier check: if this write fails or
    lands in a tier reads will not select, a later read still sees the
    ``pending`` claim, which suppresses the story just as firmly as the entry
    this would have written. Both fail safe. Still logged at ERROR, because the
    state is then carrying a claim nobody will ever settle.

    That reasoning holds only while a later read can SEE the pending claim, and
    one status breaks it. A Gist that has gone corrupt since the reservation
    verified — ``reserve_broadcast`` refuses outright on corruption, so it was
    parseable then — is one every read falls straight PAST, to a lower tier
    whose snapshot never held the claim (``save_seen_articles`` stops at the
    first tier that accepts, so a Gist-tier claim exists nowhere else). The
    guard below would then withhold the suppressing write as well, and the story
    is selectable again although a platform may already have taken it: the
    duplicate publication this mechanism exists to prevent, reached through the
    guard that protects it (Codex review on #200, filed as #201).

    So the two suppressing outcomes pass ``corrupt_gist_is_inert`` and the
    release does not. For the release, corruption is already doing what the
    write would do — the claim it wants gone is unreadable either way — so
    there is nothing to buy with an overwrite that destroys the corrupt content
    a human might otherwise recover.

    That reaches the configuration #201 describes, where a lower tier still
    holds a snapshot. It does NOT reach production, which runs on the Gist alone
    (``daily_post.yml`` sets ``GIST_ID`` and no ``STATE_STORE_URL``): there a
    corrupt Gist leaves no tier holding state, the read is untrusted, and the
    suppressing write is refused before this flag is consulted. Deliberately,
    because with nothing to derive state from the write would store the empty
    default and resume posting from no memory. What stands in for it is the
    halt: every later run needs a reservation, the reservation needs the same
    read, and ``main.py`` skips the broadcast when it is refused — so the story
    is unrecorded but nothing can repost it while the Gist stays unreadable. The
    residual window is a Gist that returns VALID BUT STALE, and no write here
    defends against that: a stale restore would overwrite a stored claim just as
    readily (Codex review on #218).

    The release does NOT fail safe, and is verified. Its whole purpose is for the
    claim to be GONE, so a shadowed write leaves the story suppressed while this
    reports success — the same over-suppression, now hidden behind a log line
    saying the opposite (Codex review, 2026-10-19). Verification can only detect
    it, not repair it: a retry would take the same shadowed path. When the
    verifying read is itself untrusted there is nothing to report either way, and
    the honest log line says so rather than claiming a settlement.
    """
    canonical = canonical_url(link)
    settled_at = datetime.now(timezone.utc).isoformat()

    def _apply(current: Dict[str, Any]) -> Dict[str, Any]:
        current.setdefault("links", [])
        claimed = next(
            (
                entry
                for entry in _prune_reservations(current.get("pending_broadcast"))
                if entry.get("link") == canonical
            ),
            None,
        )
        entries = _prune_reservations(current.get("pending_broadcast"), drop_link=canonical)
        if not delivered and attempted:
            base = claimed if claimed is not None else {"link": canonical, "reserved_at": settled_at}
            entries.append({
                **base,
                "stage": RESERVATION_STAGE_UNCERTAIN,
                "settled_at": settled_at,
            })
        current["pending_broadcast"] = entries[-MAX_BROADCAST_RESERVATIONS:]
        # persistence_stage records the same link moments later; both writes are
        # idempotent so the entry cannot be double-counted against the window.
        if delivered and canonical not in current["links"]:
            current["links"] = (current["links"] + [canonical])[-SEEN_LINKS_WINDOW:]
        return current

    # Suppressing outcomes may be written over a corrupt Gist; the release may
    # not. Both halves of that are argued in the docstring, and `attempted`
    # defaults True, so the release is the narrow case it looks like.
    suppressing = delivered or attempted
    _, tier = update_seen_articles_checked(
        _apply,
        require_gist_read=True,
        corrupt_gist_is_inert=suppressing,
    )
    if tier is None:
        SafeLogger.error(
            "broadcast_settlement_failed",
            "Could not settle the pre-broadcast reservation; it stays pending",
            reserved_link=canonical,
            delivered=delivered,
        )
        return
    if not delivered and not attempted:
        # Gated on the same flag as the read-back in `reserve_broadcast`, for the
        # same two reasons. The non-strict wrapper's empty default would make a
        # surviving claim look released, so this check would report the success
        # it exists to catch; and the aggregate `trusted` flag would do it again
        # one tier down, confirming from the store a release the recovering Gist
        # has not got (Codex review, 2026-10-19). Only a settled Gist makes
        # "the claim is gone" mean anything.
        seen_after, _read_trusted, gist_status = load_seen_articles_sourced()
        still_claimed = canonical in reserved_links(seen_after)
        # A CORRUPT Gist is one that reads fall straight PAST, so whatever the
        # tier below it returned is literally what a reader gets. A claim still
        # in that is an OBSERVED failed release, not an inconclusive check, and
        # belongs at ERROR with the others — treating it as merely unverified
        # hid a critical failure behind a warning (Codex review, 2026-10-19).
        #
        # UNREACHABLE says nothing either way, and that asymmetry is the whole
        # point of keeping the two statuses apart: the Gist answers first the
        # moment it is back, and nobody here knows what it holds.
        if still_claimed and gist_status != GIST_READ_UNREACHABLE:
            # The release was accepted by some tier and is invisible to the one
            # that answers reads, so the claim is still suppressing a story
            # nothing tried to post. Nothing to retry — the write would take the
            # same path — so the job here is to be loud rather than to report a
            # success that did not happen.
            SafeLogger.error(
                "broadcast_release_not_readable",
                "Released claim still reads back; story stays suppressed although nothing was posted",
                reserved_link=canonical,
                storage_tier=tier,
                gist_status=gist_status,
            )
            return
        if gist_status != GIST_READ_OK:
            # Either the Gist was unreachable, or it is corrupt and the claim is
            # not in what reads currently return — in which case the release
            # looks done, but the Gist's own content is unknown and may hold the
            # claim still. Detection is all this does, and here it could not.
            SafeLogger.warn(
                "broadcast_release_unverified",
                "Gist did not answer usably, so the release could not be confirmed",
                reserved_link=canonical,
                storage_tier=tier,
                gist_status=gist_status,
            )
            return
    SafeLogger.info(
        "broadcast_settled",
        "Reservation settled",
        reserved_link=canonical,
        delivered=delivered,
        attempted=attempted,
        reservation_stage=(
            RESERVATION_STAGE_UNCERTAIN if (not delivered and attempted) else None
        ),
    )


def update_replied_to(mutator: Callable[[List[str]], List[str]]) -> List[str]:
    """Lock-protected read-modify-write for replied_to.json.

    Skips the write when the read could not be trusted, mirroring
    ``update_seen_articles``. Saving an untrusted empty here would forget every
    mention already answered, and the next run would reply to those people again.
    Missing one run's bookkeeping is the cheaper failure.

    The mutator is handed the state just read under the lock. Callers must apply
    their changes to THAT value rather than returning a snapshot captured earlier
    in the run — a stale snapshot defeats both the lock and this guard.
    """
    lock_path = REPLIED_FILE.with_suffix(REPLIED_FILE.suffix + ".lock")
    with _file_lock(lock_path):
        current, trusted = load_replied_to_strict()
        if not trusted:
            SafeLogger.error(
                "replied_to_update_skipped",
                "Replied-to read was untrusted; skipping the write rather than forgetting who was answered",
            )
            return current
        updated = mutator(current)
        save_replied_to(updated)
    return updated
