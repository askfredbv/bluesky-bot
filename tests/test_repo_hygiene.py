"""Guards against committing a runtime state file.

On 2026-10-19 a test run wrote ``seen_articles.json.bak`` into the working tree,
``.gitignore`` covered only the bare ``seen_articles.json``, and ``git add -A``
swept the backup into a commit on a branch. It was caught by a human reading a
file list — not by ruff, mypy, pytest, CodeQL or a paid security scan, none of
which look at what is *tracked*.

Why a committed backup is worse than untidy. ``_load_json_with_repair_strict``
restores from ``.bak`` when the primary is missing, and returns ``from_file=True``
for that restore. On a fresh Actions runner the primary never exists (it is
gitignored), so a committed ``.bak`` is read back as *real local state* and
marked TRUSTED. The 2026-09-11 stray-backup guard does not help: it only stops
the synthesised default being marked real, and this is not the default — it is a
file. An empty-but-trusted state then overwrites the real Gist and erases every
seen link, recent topic and Pioneer cooldown.

``.gitignore`` now carries patterns for every sibling the state layer writes
(#200). This test guards the class rather than the patterns: if a new state file
is added, or a pattern is edited away, or a file is force-added, the gate fires
here instead of waiting for someone to notice.
"""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every JSON state store the bot persists. Kept as stems so the check covers the
#: file itself and every sibling: .bak, .lock, .tmp and .corrupt.<timestamp>.
STATE_STEMS = (
    "seen_articles.json",
    "replied_to.json",
    "feed_health.json",
    "post_metrics.json",
    "growth.json",
)


def _tracked_files() -> list:
    """Every path git tracks, or skip if this is not a git checkout.

    Skips rather than fails without git: a source tarball or a vendored copy is
    a legitimate way to run the suite, and failing there would be noise. CI
    always has git, which is where this gate has to hold.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"git unavailable, cannot check tracked files: {exc}")
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


@pytest.mark.parametrize("stem", STATE_STEMS)
def test_no_state_file_or_sibling_is_tracked(stem):
    """No state store, and no sibling of one, may be committed."""
    offenders = [
        path for path in _tracked_files()
        # Match the bare name, any sibling (name.json.bak / .lock / .tmp /
        # .corrupt.*), and the dot-prefixed temp form _atomic_write_json uses
        # (.seen_articles.json.<rand>.tmp).
        if Path(path).name in (stem, f".{stem}")
        or Path(path).name.startswith((f"{stem}.", f".{stem}."))
    ]
    assert not offenders, (
        f"{stem} or a sibling of it is tracked by git: {offenders}. "
        "A committed state file is restored on a fresh runner and marked "
        "TRUSTED, which overwrites the real Gist and erases the bot's history. "
        "Delete it with `git rm --cached`, and check .gitignore covers the "
        "pattern — see tests/test_repo_hygiene.py for why this matters."
    )


def test_gitignore_still_covers_every_state_sibling():
    """The patterns themselves, so the cover is not silently narrowed.

    The test above catches a file that slipped through; this one catches the
    .gitignore edit that would let the next one slip through, which is the
    failure that actually happened — the bare name was listed and the siblings
    were not.
    """
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    missing = [
        pattern for stem in STATE_STEMS
        for pattern in (f"{stem}.*", f".{stem}.*")
        if pattern not in ignored
    ]
    assert not missing, (
        f".gitignore no longer covers these state-file patterns: {missing}. "
        "The bare name alone is what let seen_articles.json.bak be committed "
        "on 2026-10-19."
    )
