"""Keeps the two copies of the review priorities from drifting apart.

``AGENTS.md`` states the review priorities for every agent that can read it.
Gemini Code Assist cannot: it reads only its own ``.gemini/`` directory, so the
same priorities are mirrored in ``.gemini/styleguide.md``. Two copies of a rule
is the exact shape of failure this repo keeps paying for — on 2026-10-07 one
wrong fact sat on five surfaces, and each fix felt like the last one.

**What this guards, and what it cannot.** It compares the priority *titles* in
both files and fails on any asymmetry: a priority renamed, added, removed or
reordered in one file and not the other. It also requires each file to number
its priorities ``1..N``, and it blanks fenced code blocks before parsing —
without those two, a mistyped number and a ``## `` inside a Markdown example
both let the check go green while the files have diverged (found by an external
review, 2026-10-22). It cannot read the prose under a title,
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
from typing import List, Sequence, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

AGENTS_MD = REPO_ROOT / "AGENTS.md"
STYLEGUIDE_MD = REPO_ROOT / ".gemini" / "styleguide.md"

#: The heading both files put the priorities under.
SECTION_PREFIX = "## Review priorities"

#: ``1. **Voice is a brand decision, not a metric decision.**`` in AGENTS.md.
AGENTS_PRIORITY = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*", re.MULTILINE)

#: ``### 1. Voice is a brand decision, not a metric decision`` in styleguide.md.
STYLEGUIDE_PRIORITY = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)

#: The opening or closing line of a fenced code block: three or more backticks
#: or tildes, indented by at most three spaces. A rule may legitimately show a
#: Markdown example, and a ``## `` or a numbered line inside that example is
#: neither a heading nor a priority.
FENCE_LINE = re.compile(r"^ {0,3}(?P<char>[`~])(?P=char){2,}")

#: A floor, not the count. Asserting the exact number would fail for the good
#: reason as well as the bad one, but an empty parse must never read as
#: agreement: two regexes that match nothing agree perfectly.
MINIMUM_PRIORITIES = 5


def _strip_fenced_blocks(text: str) -> str:
    """Blank out fenced code blocks, keeping the line count.

    Left in, a ``## `` inside a fenced example truncates the section early —
    and it truncates it at the same place in *both* files, so the two shortened
    lists match and the check goes green while everything after the fence has
    diverged. That is the dangerous direction: a false pass, not a false alarm.

    Scanned line by line rather than matched as one block, because the fence is
    a small grammar and a pattern covering part of it closes only part of the
    hole. The first version of this guard handled column-zero triple backticks,
    and a ``~~~`` fence or a three-space indent walked straight through it —
    caught in review of #224, in the same commit that introduced the guard.

    An unterminated fence runs to the end of the text, as CommonMark says it
    does. A closing fence is the same character and at least as long as the one
    that opened the block. Stripped lines become empty rather than vanishing,
    so line counts and offsets stay comparable.
    """
    out: List[str] = []
    closing = ""
    for line in text.split("\n"):
        fence = FENCE_LINE.match(line)
        marker = fence.group(0).strip() if fence else ""
        trailing = line[fence.end():] if fence else ""
        if closing:
            out.append("")
            # All three clauses are load-bearing. CommonMark closes a block on
            # the same character, at least as long, followed by nothing but
            # whitespace — and it is the last clause that bites: without it a
            # line like ``` ```not-a-close ``` *inside* the block reads as the
            # close, which exposes a later ``## `` to the section extractor and
            # reopens the very false pass this function exists to shut. Closing
            # early is not the safe direction, which is the mistake the first
            # version of this scanner made (review of #224).
            if (
                marker
                and marker[0] == closing[0]
                and len(marker) >= len(closing)
                and not trailing.strip()
            ):
                closing = ""
            continue
        if marker:
            # An opening fence may carry an info string (```markdown) — but a
            # BACKTICK fence's info string may not itself contain a backtick,
            # so ```lang`bad opens nothing and is ordinary text. Treating it as
            # an opener blanks everything to the next backtick run, and when
            # the swallowed priorities are the trailing ones the remaining
            # numbers are still 1..N, so neither the floor nor the numbering
            # check notices and both documents lose the same priorities
            # (review of #224). Tilde fences have no such restriction.
            if marker[0] == "`" and "`" in trailing:
                out.append(line)
                continue
            closing = marker
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)


def _section(path: Path) -> str:
    """The text under ``## Review priorities``, up to the next ``## `` heading."""
    text = _strip_fenced_blocks(path.read_text(encoding="utf-8"))
    start = text.find(SECTION_PREFIX)
    assert start != -1, (
        f"{path.name} no longer has a '{SECTION_PREFIX}' heading. Both files "
        "key off it; rename it in one and this mirror check goes blind."
    )
    body = text[start + len(SECTION_PREFIX):]
    end = body.find("\n## ")
    return body if end == -1 else body[:end]


def _titles(path: Path, pattern: re.Pattern) -> List[str]:
    """Priority titles in file order, normalised for comparison.

    Also checks the literal numbers. Comparing titles alone cannot see them:
    ``1, 2, 2, 4`` against ``1, 2, 3, 4`` with identical titles compares equal
    and passes. That matters because the priorities are cited by number, and
    the two files do not fail the same way — a Markdown renderer silently
    renumbers the ordered list in AGENTS.md, while ``### 3.`` in the mirror is
    literal text that renders exactly as typed. Requiring ``1..N`` in each file
    makes them agree by construction.
    """
    matches = list(pattern.finditer(_section(path)))
    titles = [
        " ".join(match.group(2).split()).rstrip(".").lower()
        for match in matches
    ]
    numbers = [int(match.group(1)) for match in matches]
    assert len(titles) >= MINIMUM_PRIORITIES, (
        f"Only {len(titles)} review priorities parsed out of {path.name}, "
        f"expected at least {MINIMUM_PRIORITIES}. The formatting of the "
        "priority headings changed and this check can no longer see them — "
        "which would otherwise let both files pass by matching nothing. Fix "
        "the pattern in tests/test_review_rule_mirror.py to match the new shape."
    )
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"The review priorities in {path.name} are numbered {numbers}, not "
        f"1..{len(numbers)}. Either a number was mistyped, or one priority is "
        "written in a shape this check cannot see and the gap is where it "
        "should have been — a title wrapped across two lines does exactly "
        "that. The priorities are cited by number across the repo, so the "
        "numbering has to be right in the file as well as consistent between "
        "the two."
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


# ── Regression cases for the two guards above ───────────────────────────────
#
# The tests above run only against the real AGENTS.md and styleguide.md, which
# are sequentially numbered and carry no fenced examples — so neither guard is
# exercised by them, and deleting either one would leave the suite green
# (caught in review of #224). These build the documents that do exercise them.

#: ``1. **Alpha.** Prose`` — the AGENTS.md shape.
AGENTS_FMT = "{n}. **{t}.** Prose that follows the title."

#: ``### 1. Alpha`` — the styleguide.md shape.
MIRROR_FMT = "### {n}. {t}"

#: Six, so a fence placed after the fifth still leaves MINIMUM_PRIORITIES
#: parseable. A fence early enough to drop below the floor fails loudly on the
#: floor instead, which is the safe direction and not the case under test.
SIX = tuple(
    (n, t) for n, t in
    enumerate(("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"), start=1)
)

#: A Markdown example holding a line that looks like the heading the section
#: extractor stops at. Three spellings CommonMark all treats as a fence.
FENCED_EXAMPLES = {
    "backticks": "```markdown\n## Not a real heading\n```",
    "tildes": "~~~markdown\n## Not a real heading\n~~~",
    "indented": "   ```markdown\n   ## Not a real heading\n   ```",
    "long": "`````markdown\n## Not a real heading\n`````",
    # A line that looks like a close but carries trailing text is still code
    # per CommonMark, so the heading below it is still inside the block.
    "fake-close": "```markdown\n```not-a-close\n## Not a real heading\n```",
}


def _doc(
    path: Path,
    numbered: Sequence[Tuple[int, str]],
    fmt: str,
    block: str = "",
    block_after: int = 0,
) -> Path:
    """Write a minimal document with a priorities section, and return its path."""
    lines = [SECTION_PREFIX, ""]
    for index, (number, title) in enumerate(numbered):
        if block and index == block_after:
            lines += [block, ""]
        lines += [fmt.format(n=number, t=title), ""]
    lines += ["## Something after", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.mark.parametrize("style", sorted(FENCED_EXAMPLES))
@pytest.mark.parametrize("fmt,pattern", [
    (AGENTS_FMT, AGENTS_PRIORITY),
    (MIRROR_FMT, STYLEGUIDE_PRIORITY),
])
def test_a_fenced_example_does_not_hide_the_priorities_below_it(
    tmp_path, style, fmt, pattern
):
    """All six must survive a fenced ``## `` sitting after the fifth.

    Without the stripping, the section is cut at the example and only five are
    seen — which is *above* MINIMUM_PRIORITIES, so the floor stays silent and
    the truncation reads as agreement.
    """
    path = _doc(
        tmp_path / f"{style}.md", SIX, fmt,
        block=FENCED_EXAMPLES[style], block_after=5,
    )
    titles = _titles(path, pattern)
    assert titles == [title.lower() for _, title in SIX], (
        f"A {style} fence hid the priorities below it: parsed {len(titles)} of "
        f"{len(SIX)}. _strip_fenced_blocks does not recognise this fence shape."
    )


@pytest.mark.parametrize("fmt,pattern", [
    (AGENTS_FMT, AGENTS_PRIORITY),
    (MIRROR_FMT, STYLEGUIDE_PRIORITY),
])
def test_a_mistyped_priority_number_is_rejected(tmp_path, fmt, pattern):
    """1, 2, 2, 4, 5, 6 must fail even though every title is intact.

    Titles alone compare equal across a renumbering, which is the hole this
    guards: the priorities are cited by number across the repo.
    """
    misnumbered = [(1, "Alpha"), (2, "Bravo"), (2, "Charlie"),
                   (4, "Delta"), (5, "Echo"), (6, "Foxtrot")]
    path = _doc(tmp_path / "misnumbered.md", misnumbered, fmt)
    with pytest.raises(AssertionError, match="are numbered"):
        _titles(path, pattern)


def test_a_divergence_below_a_fence_is_still_visible(tmp_path):
    """The whole failure, end to end: fence after the fifth, sixth diverged."""
    mirrored = list(SIX[:-1]) + [(6, "Foxtrot has drifted")]
    canonical = _titles(
        _doc(tmp_path / "AGENTS.md", SIX, AGENTS_FMT,
             block=FENCED_EXAMPLES["tildes"], block_after=5),
        AGENTS_PRIORITY,
    )
    mirror = _titles(
        _doc(tmp_path / "styleguide.md", mirrored, MIRROR_FMT,
             block=FENCED_EXAMPLES["tildes"], block_after=5),
        STYLEGUIDE_PRIORITY,
    )
    assert canonical != mirror, (
        "Both documents were truncated at the fenced example, so the shortened "
        "lists matched and the divergence in priority 6 went unseen. This is "
        "the false pass the stripping exists to prevent."
    )


#: Not a fence at all: a backtick fence's info string may not itself contain a
#: backtick, so CommonMark leaves this line as ordinary text.
NOT_AN_OPENER = "```lang`bad"


@pytest.mark.parametrize("fmt,pattern", [
    (AGENTS_FMT, AGENTS_PRIORITY),
    (MIRROR_FMT, STYLEGUIDE_PRIORITY),
])
def test_a_line_that_only_looks_like_a_fence_swallows_nothing(tmp_path, fmt, pattern):
    """``` ```lang`bad ``` is text, and the priorities after it must survive.

    Treated as an opener it blanks everything to the next backtick run. The
    trailing case is the one that gets through the other guards: drop the LAST
    priority and the remaining numbers are still 1..N, so the numbering check
    is satisfied and five survivors keep the floor quiet. Drop a middle one and
    the numbering check already catches it — which is why this case is built
    with the swallowed priority last.
    """
    path = _doc(
        tmp_path / "looks-like-a-fence.md", SIX, fmt,
        block=NOT_AN_OPENER, block_after=5,
    )
    titles = _titles(path, pattern)
    assert titles == [title.lower() for _, title in SIX], (
        f"A line that is not a valid fence opener swallowed the priorities "
        f"after it: parsed {len(titles)} of {len(SIX)}."
    )
