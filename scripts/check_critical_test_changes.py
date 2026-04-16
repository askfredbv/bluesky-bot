#!/usr/bin/env python3
"""Require focused test updates when critical modules change in PRs."""

from __future__ import annotations

import subprocess
import sys

CRITICAL_FILES = {"src/utils.py", "src/agents.py", "main.py"}
FOCUSED_TEST_PATTERNS = (
    "tests/test_utils",
    "tests/test_agents",
    "tests/test_main",
)


def git_changed_files(base_ref: str, head_sha: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"origin/{base_ref}...{head_sha}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def has_focused_test_change(paths: list[str]) -> bool:
    return any(path.startswith(FOCUSED_TEST_PATTERNS) for path in paths)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: check_critical_test_changes.py <base_ref> <head_sha>")
        return 2

    base_ref, head_sha = sys.argv[1], sys.argv[2]
    changed_files = git_changed_files(base_ref, head_sha)

    changed_critical = sorted(CRITICAL_FILES.intersection(changed_files))
    if not changed_critical:
        print("No critical modules changed; focused-test policy not required.")
        return 0

    print("Critical modules changed:")
    for path in changed_critical:
        print(f"  - {path}")

    if has_focused_test_change(changed_files):
        print("Focused tests were updated in this PR.")
        return 0

    print("\n❌ This PR changes critical modules but no focused tests were updated.")
    print(
        "Add or adjust at least one focused test file matching: "
        f"{', '.join(FOCUSED_TEST_PATTERNS)}*"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
