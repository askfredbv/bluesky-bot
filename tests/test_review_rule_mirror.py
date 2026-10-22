"""Keeps the two copies of the review priorities from drifting apart.

``AGENTS.md`` states the review priorities for every agent that can read it.
Gemini Code Assist cannot: it reads only its own ``.gemini/`` directory, so the
same priorities are mirrored in ``.gemini/styleguide.md``. Two copies of a rule
is the exact shape of failure this repo keeps paying for — on 2026-10-07 one
wrong fact sat on five surfaces, and each fix felt like the last one.

**What this guards, and what it cannot.** It compares the priority *titles* in
both files and fails on any asymmetry: a priority renamed, added, removed or
reordered in one file and not the other. It also requires each file to label
its priorities ``1`` to ``N``, literally and in ASCII. It cannot read the prose
under a title, so a rule sharpened in ``AGENTS.md`` while the mirror keeps the
old wording still passes here. That hole is known and deliberate — a checksum
over the section would catch it, at the cost of failing CI on every edit until
a human re-syncs both. This test is the cheap half; ``AGENTS.md`` carries the
note that the two change in the same commit.

The titles must match exactly, modulo case, surrounding whitespace and a
trailing period. That strictness is the point: a loose comparison would let the
two drift in wording while reporting green, which is worse than no test.
"""
import re
from pathlib import Path
from typing import List, Sequence, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENTS_MD = REPO_ROOT / "AGENTS.md"
STYLEGUIDE_MD = REPO_ROOT / ".gemini" / "styleguide.md"

#: The heading both files put the priorities under. A prefix, because AGENTS.md
#: continues it with "— the things a generic reviewer misses".
SECTION_START = "## Review priorities"

#: The heading that ends the section, named rather than "the next ``## ``".
#: See _section for why that distinction is the whole design.
SECTION_END = "## How to review"

#: and matched as a heading LINE, not as text. A plain ``find`` also hits the
#: terminator quoted inside an example or inline code, which truncates the
#: section at that point in both files — the same false pass, one layer up.
SECTION_END_LINE = re.compile(r"^##\s+How to review\s*$", re.MULTILINE)

#: ``1. **Voice is a brand decision, not a metric decision.**`` in AGENTS.md.
#: ``\d`` deliberately, not ``[0-9]``: capture any decimal-looking label and let
#: the exact-string check below reject the wrong ones. Narrowing the capture
#: instead makes a Unicode-digit label *invisible* rather than rejected, so a
#: priority written ``७.`` would silently not exist.
AGENTS_PRIORITY = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*", re.MULTILINE)

#: ``### 1. Voice is a brand decision, not a metric decision`` in styleguide.md.
STYLEGUIDE_PRIORITY = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)

#: A floor, not the count. Asserting the exact number would fail for the good
#: reason as well as the bad one, but an empty parse must never read as
#: agreement: two regexes that match nothing agree perfectly.
MINIMUM_PRIORITIES = 5


def _section(path: Path) -> str:
    """The text between the two named headings.

    Bounded by a *named* terminator rather than "the next ``## ``", which is
    the second design of this function and the point of the whole file.

    The first design stopped at the next ``## `` and therefore had to
    understand Markdown fences, because a ``## `` inside a fenced example
    truncated the section — at the same point in *both* files, so the two
    shortened lists matched and the check passed over a divergence below. Four
    rounds of review found four separate clauses of the fence grammar that the
    stripper got wrong, each one a fresh variation of that same false pass:
    tilde and indented fences, closing fences with trailing text, backtick
    info strings containing a backtick, and finally an unterminated fence
    inside a list item, which CommonMark closes at the end of the item.

    The fifth one is what settled it. A grammar that needs five corrections is
    not being approximated badly, it is the wrong thing to be doing here, and
    the fix is to stop needing it. Naming the terminator deletes the problem
    rather than parsing around it: a ``## `` inside an example is no longer
    special, because only this exact heading ends the section. The fence
    handling is gone with it — roughly forty lines removed, not added.

    Both headings must be present and in order. A missing one is loud.

    **What this still does not cover, stated rather than left to be found.** The
    terminator is matched as a heading line, so the text quoted in an example or
    in inline code is not mistaken for it. A fenced block containing the exact
    line ``## How to review`` at column zero, in *both* files, below the fifth
    priority, would still truncate both and read as agreement. Closing that
    would mean understanding fences again, which is the thing this design
    exists to avoid, and it requires someone to paste this file's own
    terminator into a code example in both documents. Known, accepted, not
    forgotten.
    """
    text = path.read_text(encoding="utf-8")
    start = text.find(SECTION_START)
    assert start != -1, (
        f"{path.name} no longer has a '{SECTION_START}' heading. Both files "
        "key off it; rename it in one and this mirror check goes blind."
    )
    body_at = start + len(SECTION_START)
    terminator = SECTION_END_LINE.search(text, body_at)
    end = terminator.start() if terminator else -1
    assert end != -1, (
        f"{path.name} has no '{SECTION_END}' heading after '{SECTION_START}'. "
        "That heading is what ends the priorities section. Without it the "
        "section would run to the end of the file and sweep in anything "
        "numbered like a priority, so this fails rather than guessing."
    )
    return text[body_at:end]


def _titles(path: Path, pattern: re.Pattern) -> List[str]:
    """Priority titles in file order, normalised for comparison.

    Also checks the labels, as the literal strings they are. Comparing titles
    alone cannot see them: ``1, 2, 2, 4`` against ``1, 2, 3, 4`` with identical
    titles compares equal and passes. That matters because the priorities are
    cited by number, and the two files do not fail the same way — a Markdown
    renderer silently renumbers the ordered list in AGENTS.md, while ``### 3.``
    in the mirror is literal text that renders exactly as typed.

    The comparison is against the exact ASCII label rather than ``int()`` of
    it, because ``int`` normalises away differences a reader can see: ``01``
    and ``1`` are the same integer and different labels.
    """
    matches = list(pattern.finditer(_section(path)))
    titles = [
        " ".join(match.group(2).split()).rstrip(".").lower()
        for match in matches
    ]
    labels = [match.group(1) for match in matches]
    assert len(titles) >= MINIMUM_PRIORITIES, (
        f"Only {len(titles)} review priorities parsed out of {path.name}, "
        f"expected at least {MINIMUM_PRIORITIES}. The formatting of the "
        "priority headings changed and this check can no longer see them — "
        "which would otherwise let both files pass by matching nothing. Fix "
        "the pattern in tests/test_review_rule_mirror.py to match the new shape."
    )
    assert labels == [str(n) for n in range(1, len(labels) + 1)], (
        f"The review priorities in {path.name} are labelled {labels}, not "
        f"1 to {len(labels)}. Either a label was mistyped or padded, or one "
        "priority is written in a shape this check cannot see and the gap is "
        "where it should have been — a title wrapped across two lines does "
        "exactly that. The priorities are cited by number across the repo, so "
        "the labels have to be right in the file as well as consistent "
        "between the two."
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


# ── Regression cases for the guards above ───────────────────────────────────
#
# The tests above run only against the real AGENTS.md and styleguide.md, which
# are cleanly labelled and carry no awkward Markdown — so they exercise none of
# the guards, and deleting one would leave the suite green. These build the
# documents that do exercise them.

#: ``1. **Alpha.** Prose`` — the AGENTS.md shape.
AGENTS_FMT = "{n}. **{t}.** Prose that follows the title."

#: ``### 1. Alpha`` — the styleguide.md shape.
MIRROR_FMT = "### {n}. {t}"

BOTH_SHAPES = [(AGENTS_FMT, AGENTS_PRIORITY), (MIRROR_FMT, STYLEGUIDE_PRIORITY)]

#: Six, so dropping one still leaves MINIMUM_PRIORITIES parseable and the floor
#: cannot mask what is being tested.
SIX: Sequence[Tuple[str, str]] = tuple(
    (str(n), t) for n, t in
    enumerate(("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"), start=1)
)

#: Markdown that a previous design of _section mis-parsed, in every spelling
#: four review rounds turned up. None of them is special any more: the section
#: ends at SECTION_END and nowhere else. Kept as cases precisely because they
#: are the shapes that used to break it.
AWKWARD_MARKDOWN = {
    "backtick-fence": "```markdown\n## Not a real heading\n```",
    "tilde-fence": "~~~markdown\n## Not a real heading\n~~~",
    "indented-fence": "   ```markdown\n   ## Not a real heading\n   ```",
    "long-fence": "`````markdown\n## Not a real heading\n`````",
    "fence-with-fake-close": "```markdown\n```not-a-close\n## Not a real heading\n```",
    "backtick-in-info-string": "```lang`bad\n## Not a real heading\n```",
    "unterminated-fence-in-a-list": "- an item\n\n  ```markdown\n  ## Not a real heading",
    "bare-heading": "## Not a real heading",
    # The terminator's own text, quoted rather than used as a heading.
    "terminator-in-inline-code": "See `## How to review` for the rest.",
    "terminator-mid-sentence": "The section ends at ## How to review, below.",
}


def _doc(
    path: Path,
    labelled: Sequence[Tuple[str, str]],
    fmt: str,
    block: str = "",
    block_after: int = 0,
) -> Path:
    """Write a minimal document with a priorities section, and return its path."""
    lines = [SECTION_START, ""]
    for index, (label, title) in enumerate(labelled):
        if block and index == block_after:
            lines += [block, ""]
        lines += [fmt.format(n=label, t=title), ""]
    lines += [SECTION_END, "", "Prose after the section.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.mark.parametrize("shape", sorted(AWKWARD_MARKDOWN))
@pytest.mark.parametrize("fmt,pattern", BOTH_SHAPES)
def test_awkward_markdown_does_not_truncate_the_section(
    tmp_path, shape, fmt, pattern
):
    """All six survive any Markdown that merely looks like the terminator.

    Every one of these shapes broke an earlier design that stopped at the next
    ``## ``. The section now ends at SECTION_END, so none of them is special.
    """
    path = _doc(
        tmp_path / f"{shape}.md", SIX, fmt,
        block=AWKWARD_MARKDOWN[shape], block_after=5,
    )
    titles = _titles(path, pattern)
    assert titles == [title.lower() for _, title in SIX], (
        f"{shape} truncated the section: parsed {len(titles)} of {len(SIX)}. "
        "Only SECTION_END should end it."
    )


@pytest.mark.parametrize("fmt,pattern", BOTH_SHAPES)
def test_a_missing_terminator_fails_loudly(tmp_path, fmt, pattern):
    """No SECTION_END means the bound is unknown, which must not read as fine."""
    path = tmp_path / "no-terminator.md"
    body = _doc(path, SIX, fmt).read_text(encoding="utf-8")
    path.write_text(body.replace(SECTION_END, "## Something else"), encoding="utf-8")
    with pytest.raises(AssertionError, match="has no"):
        _titles(path, pattern)


@pytest.mark.parametrize("labels,why", [
    (["1", "2", "2", "4", "5", "6"], "a mistyped label"),
    (["01", "02", "03", "04", "05", "06"], "zero padding, which int() hides"),
    (["1", "2", "3", "4", "5", "७"], "a Unicode digit, which must be "
                                           "captured and rejected, not skipped"),
])
@pytest.mark.parametrize("fmt,pattern", BOTH_SHAPES)
def test_labels_must_be_exactly_one_to_n(tmp_path, labels, why, fmt, pattern):
    """Titles compare equal across a relabelling, so the labels are checked too."""
    relabelled = list(zip(labels, [title for _, title in SIX]))
    path = _doc(tmp_path / "relabelled.md", relabelled, fmt)
    with pytest.raises(AssertionError, match="are labelled"):
        _titles(path, pattern)


def test_a_divergence_below_awkward_markdown_is_still_visible(tmp_path):
    """The whole failure end to end: a fenced heading, then a diverged sixth."""
    mirrored = list(SIX[:-1]) + [("6", "Foxtrot has drifted")]
    block = AWKWARD_MARKDOWN["unterminated-fence-in-a-list"]
    canonical = _titles(
        _doc(tmp_path / "AGENTS.md", SIX, AGENTS_FMT, block=block, block_after=5),
        AGENTS_PRIORITY,
    )
    mirror = _titles(
        _doc(tmp_path / "styleguide.md", mirrored, MIRROR_FMT, block=block, block_after=5),
        STYLEGUIDE_PRIORITY,
    )
    assert canonical != mirror, (
        "Both documents lost the sixth priority to the same piece of awkward "
        "Markdown, so the shortened lists matched and the divergence went "
        "unseen. That is the false pass this file exists to prevent."
    )
