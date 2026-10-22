"""Keeps the two copies of the review priorities from drifting apart.

``AGENTS.md`` states the review priorities for every agent that can read it.
Gemini Code Assist cannot: it reads only its own ``.gemini/`` directory, so the
same priorities are mirrored in ``.gemini/styleguide.md``. Two copies of a rule
is the exact shape of failure this repo keeps paying for — on 2026-10-07 one
wrong fact sat on five surfaces, and each fix felt like the last one.

**What this guards, and what it cannot.** It compares the priority *titles* in
both files and fails on any asymmetry: a priority renamed, added, removed or
reordered in one file and not the other. It cannot read the prose under a title,
so a rule sharpened in ``AGENTS.md`` while the mirror keeps the old wording
still passes here. That hole is known and deliberate — a checksum over the
section would catch it, at the cost of failing CI on every edit until a human
re-syncs both. This test is the cheap half; ``AGENTS.md`` carries the note that
the two change in the same commit.

The titles must match exactly, modulo case, surrounding whitespace and a
trailing period. That strictness is the point: a loose comparison would let the
two drift in wording while reporting green, which is worse than no test.
"""
import re
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENTS_MD = REPO_ROOT / "AGENTS.md"
STYLEGUIDE_MD = REPO_ROOT / ".gemini" / "styleguide.md"

#: The heading both files put the priorities under.
SECTION_PREFIX = "## Review priorities"

#: ``1. **Voice is a brand decision, not a metric decision.**`` in AGENTS.md.
AGENTS_PRIORITY = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*", re.MULTILINE)

#: ``### 1. Voice is a brand decision, not a metric decision`` in styleguide.md.
STYLEGUIDE_PRIORITY = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)

#: A floor, not the count. Asserting the exact number would fail for the good
#: reason as well as the bad one, but an empty parse must never read as
#: agreement: two regexes that match nothing agree perfectly.
MINIMUM_PRIORITIES = 5


def _section(path: Path) -> str:
    """The text under ``## Review priorities``, up to the next ``## `` heading."""
    text = path.read_text(encoding="utf-8")
    start = text.find(SECTION_PREFIX)
    assert start != -1, (
        f"{path.name} no longer has a '{SECTION_PREFIX}' heading. Both files "
        "key off it; rename it in one and this mirror check goes blind."
    )
    body = text[start + len(SECTION_PREFIX):]
    end = body.find("\n## ")
    return body if end == -1 else body[:end]


def _titles(path: Path, pattern: re.Pattern) -> List[str]:
    """Priority titles in file order, normalised for comparison."""
    titles = [
        " ".join(match.group(2).split()).rstrip(".").lower()
        for match in pattern.finditer(_section(path))
    ]
    assert len(titles) >= MINIMUM_PRIORITIES, (
        f"Only {len(titles)} review priorities parsed out of {path.name}, "
        f"expected at least {MINIMUM_PRIORITIES}. The formatting of the "
        "priority headings changed and this check can no longer see them — "
        "which would otherwise let both files pass by matching nothing. Fix "
        "the pattern in tests/test_review_rule_mirror.py to match the new shape."
    )
    return titles


def test_styleguide_mirrors_every_agents_priority():
    """Same priorities, same order, in both files.

    Failing here does not mean the change is wrong. It means it landed in one
    of the two files and the other one still claims otherwise.
    """
    canonical = _titles(AGENTS_MD, AGENTS_PRIORITY)
    mirrored = _titles(STYLEGUIDE_MD, STYLEGUIDE_PRIORITY)

    missing = [title for title in canonical if title not in mirrored]
    extra = [title for title in mirrored if title not in canonical]

    assert not missing and not extra, (
        "AGENTS.md and .gemini/styleguide.md no longer state the same review "
        f"priorities.\n  In AGENTS.md but not the mirror: {missing or 'none'}"
        f"\n  In the mirror but not AGENTS.md: {extra or 'none'}\n"
        "AGENTS.md is canonical. Gemini Code Assist reads only .gemini/, so a "
        "priority that lives in one file is enforced by some reviewers and not "
        "others. Update both in this commit."
    )
    assert canonical == mirrored, (
        "Both files list the same review priorities, but not in the same "
        f"order.\n  AGENTS.md: {canonical}\n  styleguide: {mirrored}\n"
        "The priorities are referred to by number (AGENTS.md §1, styleguide "
        "§8), so a reordering silently repoints every reference."
    )


def test_gemini_config_and_styleguide_are_both_present():
    """Neither half of the Gemini Code Assist setup is useful without the other.

    ``config.yaml`` without ``styleguide.md`` reviews with Gemini Code Assist's
    defaults and none of the rules above; ``styleguide.md`` without
    ``config.yaml`` reviews at the default MEDIUM threshold with no comment cap,
    which is the noise this repo configured away.
    """
    config = REPO_ROOT / ".gemini" / "config.yaml"
    missing = [
        path.name for path in (config, STYLEGUIDE_MD) if not path.is_file()
    ]
    assert not missing, (
        f"Missing from .gemini/: {missing}. Gemini Code Assist is installed on "
        "this repo (Developer Connect connection ghbskbot2) and falls back to "
        "its defaults for whichever file is absent, without warning anyone."
    )
