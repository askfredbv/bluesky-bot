"""Open, update or close one GitHub issue for advisories against the runtime lockfile.

`dependency-audit.yml` has run `pip-audit` on every PR, every push to main and
once a week since 2026-09-11. A red check is a fine signal on a PR and no signal
at all between them: on 2026-10-21 nine advisories (eight Pillow CVEs and a
certifi CA compromise) sat on main overnight, and what surfaced them was a
docs-only PR failing a check it had nothing to do with. The weekly cron was due
the following Monday, so the real exposure was five days of a red main nobody
could see. The workflow comment claimed "the weekly run catches advisories
published between PRs"; it does, up to a week late.

Same shape as scripts/report_feed_health.py, for the same reason: a finding that
lives only in a run nobody opens is not a finding. One issue, labelled
`dependency-audit`:
  - advisories found, no open issue      -> open one listing them
  - advisories found, issue already open -> rewrite its body if it changed, and
                                            comment only when the SET of
                                            advisory IDs changed, so it does not
                                            nag on every daily run
  - nothing found, issue open            -> close it with a comment
  - nothing found, no issue              -> nothing

The body lists advisory IDs and fix versions, not counts, so it changes when the
facts do rather than on every run.

An unreadable or malformed audit file changes nothing. "I could not run the
audit" must never look like "the lockfile is clean" and close a real issue --
the same distinction #200 drew for the state Gist, where collapsing the two
would have made the guard report the very story it exists to protect.

Run: `python -m scripts.report_dependency_audit <audit.json>`, with GH_TOKEN
(and GH_REPO) for `gh`. The path may also come from AUDIT_JSON; it defaults to
`audit.json` in the working directory. Generate it with:

    pip-audit -r requirements.txt --no-deps --format json --output audit.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

LABEL = "dependency-audit"
TITLE = "Dependency audit: known vulnerabilities in the runtime lockfile"
MARKER = "<!-- dependency-audit-report -->"

Runner = Callable[[Sequence[str]], str]


def _gh(args: Sequence[str]) -> str:
    """Run `gh` and return its stdout; raises if the command fails."""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def parse_audit(payload: Any) -> List[Dict[str, str]]:
    """Flatten pip-audit's JSON into one row per (package, advisory).

    Shape, verified against pip-audit 2.10.1:
        {"dependencies": [{"name", "version", "vulns": [{"id", "fix_versions"}]}]}

    A payload that is not that shape raises, because the caller must treat
    "I could not read the audit" as no signal rather than as a clean lockfile.
    Individual malformed entries are skipped instead: one odd row should not
    discard the advisories either side of it.
    """
    if not isinstance(payload, dict):
        raise ValueError("audit payload is not an object")
    deps = payload.get("dependencies")
    if not isinstance(deps, list):
        raise ValueError("audit payload has no dependencies list")
    # The dangerous shape is not a payload we reject outright, it is one that
    # still parses and yields nothing: that reads as "clean" and CLOSES the
    # issue. A clean audit says `vulns: []` on every dependency, so the key
    # being absent everywhere is drift, not good news.
    if deps and not any(isinstance(d, dict) and isinstance(d.get("vulns"), list) for d in deps):
        raise ValueError("no dependency carries a vulns list; the audit shape changed")
    rows: List[Dict[str, str]] = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        name, version = dep.get("name"), dep.get("version")
        vulns = dep.get("vulns")
        if not isinstance(name, str) or not isinstance(vulns, list):
            continue
        for vuln in vulns:
            if not isinstance(vuln, dict) or not isinstance(vuln.get("id"), str):
                continue
            fixes = vuln.get("fix_versions")
            fixes = [f for f in fixes if isinstance(f, str)] if isinstance(fixes, list) else []
            rows.append({
                "package": name,
                "version": version if isinstance(version, str) else "?",
                "id": vuln["id"],
                "fix": ", ".join(fixes) if fixes else "no fix released",
            })
    return rows


def render_body(findings: List[Dict[str, str]]) -> str:
    lines = [
        MARKER,
        "",
        "`pip-audit` reports known vulnerabilities against `requirements.txt`, "
        "which is what production installs:",
        "",
        "| Package | Installed | Advisory | Fixed in |",
        "| --- | --- | --- | --- |",
    ]
    for row in sorted(findings, key=lambda r: (r["package"], r["id"])):
        lines.append(f"| {row['package']} | {row['version']} | {row['id']} | {row['fix']} |")
    lines += [
        "",
        "Raise the constraint in `pyproject.toml` if the fix version sits outside it, then "
        "regenerate both lockfiles. **Check that first:** on 2026-10-21 the Pillow fix was "
        "13.0.0 against a declared `<13`, so regenerating the lockfile reported success and "
        "closed nothing.",
        "",
        "A package with no fix released needs a decision, not a bump: say in this issue "
        "whether the path is reachable here.",
        "",
        "This issue closes itself on the first run where `pip-audit` reports nothing.",
    ]
    return "\n".join(lines) + "\n"


def advisory_ids_in(body: str) -> List[str]:
    """The advisory IDs listed in a report body, for change detection."""
    ids = []
    for line in body.splitlines():
        if not line.startswith("| ") or line.startswith("| Package") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) >= 5 and cells[3]:
            ids.append(cells[3])
    return sorted(ids)


def plan(findings: List[Dict[str, str]], open_issue: Optional[Dict[str, Any]]) -> str:
    """What to do: "open", "update", "update+comment", "close" or "nothing"."""
    if not findings:
        return "close" if open_issue is not None else "nothing"
    if open_issue is None:
        return "open"
    old_body = open_issue.get("body") or ""
    if old_body.strip() == render_body(findings).strip():
        return "nothing"
    if advisory_ids_in(old_body) != sorted(row["id"] for row in findings):
        return "update+comment"
    return "update"


def find_open_issues(gh: Runner) -> List[Dict[str, Any]]:
    """Every open issue carrying the label, oldest first.

    Not `--limit 1`. Two reporters can race -- a push to main overlapping the
    cron -- and both open an issue; with a limit of one, every later run manages
    whichever it happens to see and the other stays open forever, including
    after the lockfile comes back clean. The job is serialised by a concurrency
    group so the race should not happen, and this reconciles it if it ever does,
    because an alert that needs manual tidying is an alert that gets ignored.
    """
    out = gh(["issue", "list", "--label", LABEL, "--state", "open",
              "--json", "number,body", "--limit", "100"])
    issues = json.loads(out or "[]")
    return sorted((i for i in issues if isinstance(i, dict) and "number" in i),
                  key=lambda i: i["number"])


def report(findings: List[Dict[str, str]], gh: Runner = _gh) -> str:
    """Apply ``plan`` through ``gh``; return the action taken."""
    gh(["label", "create", LABEL, "--color", "B60205",
        "--description", "pip-audit found a known vulnerability", "--force"])
    issues = find_open_issues(gh)
    issue = issues[0] if issues else None
    # Keep the oldest and fold any duplicate into it, so "one self-closing
    # issue" stays true even after a race.
    for duplicate in issues[1:]:
        assert issue is not None
        gh(["issue", "close", str(duplicate["number"]),
            "--comment", f"Duplicate of #{issue['number']}, which is the one this "
                         f"job keeps up to date. Closing."])
    action = plan(findings, issue)
    if action == "open":
        gh(["issue", "create", "--title", TITLE, "--label", LABEL, "--body", render_body(findings)])
    elif action in ("update", "update+comment"):
        assert issue is not None
        number = str(issue["number"])
        gh(["issue", "edit", number, "--body", render_body(findings)])
        if action == "update+comment":
            now = ", ".join(sorted(row["id"] for row in findings))
            gh(["issue", "comment", number,
                "--body", f"The set of advisories changed. Now reported: {now}."])
    elif action == "close":
        assert issue is not None
        gh(["issue", "close", str(issue["number"]),
            "--comment", "`pip-audit` reports no known vulnerabilities against the runtime "
                         "lockfile. Closing."])
    return action


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    path = args[0] if args else os.environ.get("AUDIT_JSON") or "audit.json"
    try:
        with open(path, encoding="utf-8") as handle:
            findings = parse_audit(json.load(handle))
    except Exception as exc:  # noqa: BLE001 - any failure here means "no signal"
        print(f"dependency audit report: cannot read {path}, leaving the issue alone: "
              f"{type(exc).__name__}: {str(exc)[:200]}")
        return 0
    action = report(findings)
    print(f"dependency audit report: {len(findings)} advisories, action={action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
