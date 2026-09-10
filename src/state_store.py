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

from src.config import PIONEER_COOLDOWN_DAYS, REPLIED_FILE, SEEN_FILE
from src.file_lock import file_lock
from src.logger import SafeLogger

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
    """
    gist_id = os.environ.get("GIST_ID", "").strip()
    if not gist_id:
        return None, True  # local dev, no Gist configured — empty is legitimate
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
        return None, False
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict) or filename not in files:
        # Gist reachable, but this file has never been written. Genuinely
        # absent — trustworthy empty (e.g. the first proactive run).
        return None, True
    try:
        return json.loads(files[filename]["content"]), True
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
        return None, False
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
def _load_json_with_repair(
    file_path: Path,
    default_factory: Callable[[], T],
    *,
    migrate_list_to_seen_shape: bool = False
) -> T:
    """
    Load JSON with corruption repair.
    - If decode fails, try restoring from .bak.
    - If backup is also invalid/missing, preserve corrupt file and reset to default.
    """
    default_data = default_factory()
    if not file_path.exists():
        return default_data

    backup_path = file_path.with_suffix(file_path.suffix + ".bak")
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        SafeLogger.error("json_corrupt_detected", "Corrupt JSON detected", exception=e, file_path=str(file_path))
        restored_data = None
        if backup_path.exists():
            try:
                with open(backup_path, "r") as backup_file:
                    restored_data = json.load(backup_file)
                _atomic_write_json(file_path, restored_data)
                SafeLogger.warn("json_restored_from_backup", "Restored file from backup", file_path=str(file_path), backup_path=str(backup_path))
            except Exception as backup_error:
                SafeLogger.error("json_backup_restore_failed", "Backup restore failed", exception=backup_error, file_path=str(file_path))

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
            return default_data
        data = restored_data
    except Exception as e:
        SafeLogger.error("json_load_failed", "Failed to load JSON", exception=e, file_path=str(file_path))
        return default_data

    if migrate_list_to_seen_shape and isinstance(data, list):
        migrated = {"links": data, "recent_topics": [], "pioneer_recent": []}
        _atomic_write_json(file_path, migrated)
        return migrated  # type: ignore[return-value]

    return data
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
    default: Dict[str, Any] = {"links": [], "recent_topics": [], "pioneer_recent": []}
    # 1. Try Gist
    gist_data, gist_trusted = _load_gist_state_strict("seen_articles.json")
    if isinstance(gist_data, dict) and "links" in gist_data and "recent_topics" in gist_data:
        return _ensure_pioneer_field(gist_data), True
    # 2. Existing STATE_STORE_URL fallback
    remote_data = _load_state_from_store("seen_articles")
    if isinstance(remote_data, dict) and "links" in remote_data and "recent_topics" in remote_data:
        return _ensure_pioneer_field(remote_data), True
    if isinstance(remote_data, list):
        migrated = {"links": remote_data, "recent_topics": [], "pioneer_recent": []}
        _save_state_to_store("seen_articles", migrated)
        return migrated, True
    # 3. Local file fallback
    local_exists = SEEN_FILE.exists() or SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".bak").exists()
    data = _load_json_with_repair(
        SEEN_FILE,
        lambda: default,
        migrate_list_to_seen_shape=True
    )
    if isinstance(data, dict) and "links" in data and "recent_topics" in data:
        # An absent local file yields the well-shaped default, which is
        # indistinguishable from genuinely-empty state. Real local state (the
        # file is there) is trustworthy on its own; the synthesised default is
        # only trustworthy if the Gist read ahead of it actually succeeded.
        return _ensure_pioneer_field(data), (local_exists or gist_trusted)
    # 4. Nothing supplied real state. Whether the empty default is trustworthy
    #    depends entirely on whether the Gist read succeeded: a reachable Gist
    #    with no file yet is a legitimate first run, an unreachable one is not.
    if not gist_trusted:
        SafeLogger.error(
            "seen_articles_read_untrusted",
            "Seen-state read failed and no fallback tier had state; treating empty as UNKNOWN",
        )
        return default, False
    SafeLogger.warn("seen_articles_format_repaired", "Unexpected seen_articles format detected; repairing to default shape")
    _atomic_write_json(SEEN_FILE, default)
    return default, True


def load_seen_articles() -> Dict[str, Any]:
    """Load seen state including links, recent topics, and pioneer cooldown.

    Thin wrapper over ``load_seen_articles_strict`` that drops the trust flag,
    preserving the original contract for read-only callers. Anything that will
    WRITE the result back must use the strict variant and honour ``trusted``.
    """
    return load_seen_articles_strict()[0]


def save_seen_articles(seen_data: Dict[str, Any]) -> None:
    if _save_gist_state("seen_articles.json", seen_data):
        return
    if _save_state_to_store("seen_articles", seen_data):
        return
    try:
        _atomic_write_json(SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".bak"), seen_data)
        _atomic_write_json(SEEN_FILE, seen_data)
    except Exception as e:
        SafeLogger.error("seen_articles_save_failed", "Failed to save seen articles", exception=e)
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
    local_exists = REPLIED_FILE.exists() or REPLIED_FILE.with_suffix(REPLIED_FILE.suffix + ".bak").exists()
    data: List[str] = _load_json_with_repair(REPLIED_FILE, lambda: [])
    if isinstance(data, list):
        # An absent local file yields the empty list, which is indistinguishable
        # from a genuinely empty history. Real local state is trustworthy on its
        # own; the synthesised empty is only trustworthy if the Gist read ahead of
        # it actually succeeded.
        return data, (local_exists or gist_trusted)
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
def update_seen_articles(mutator: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
    """Lock-protected read-modify-write for seen_articles.json.

    Skips the write entirely when the read could not be trusted. An untrusted
    read yields the empty default, and saving that would replace the real state
    with nothing — one transient Gist failure costing every seen link, recent
    topic and Pioneer cooldown. Missing one run's bookkeeping is the cheaper
    failure, the same trade AGENTS.md principle 2 makes for content.

    The mutator is handed the state just read under the lock. Callers must apply
    their changes to THAT value rather than returning a snapshot captured earlier
    in the run — a stale snapshot defeats both the lock and this guard.
    """
    lock_path = SEEN_FILE.with_suffix(SEEN_FILE.suffix + ".lock")
    with _file_lock(lock_path):
        current, trusted = load_seen_articles_strict()
        if not trusted:
            SafeLogger.error(
                "seen_articles_update_skipped",
                "Seen-state read was untrusted; skipping the write rather than erasing history",
            )
            return current
        updated = mutator(current)
        save_seen_articles(updated)
    return updated
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
