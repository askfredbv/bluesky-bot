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

    Anything unreadable raises, down to a single advisory without an id,
    because the caller must treat "I could not read the audit" as no signal
    rather than as a clean lockfile -- and a report missing rows is
    indistinguishable from one that had none to miss.
    """
    if not isinstance(payload, dict):
        raise ValueError("audit payload is not an object")
    deps = payload.get("dependencies")
    if not isinstance(deps, list):
        raise ValueError("audit payload has no dependencies list")

    # ONE rule, applied to every record: if it cannot be read, there is no
    # signal, so raise. The caller turns that into "leave the issue alone".
    #
    # The first two attempts here both got this wrong in the same direction
    # (Codex, #215). They SKIPPED unreadable records, which silently converts a
    # partial report into an apparently complete one -- and an empty report
    # closes the issue. The second attempt also guarded with `any`, so one
    # readable `vulns: []` record vouched for a payload whose other record
    # carried a live CVE under a renamed key.
    #
    # For an alert whose failure mode is silent closure, under-reporting is the
    # expensive direction and over-strictness is the cheap one: a raise leaves a
    # real issue standing and merely un-refreshed.
    rows: List[Dict[str, str]] = []
    for dep in deps:
        if not isinstance(dep, dict):
            raise ValueError("a dependency record is not an object; the audit shape changed")
        name, version, vulns = dep.get("name"), dep.get("version"), dep.get("vulns")
        # Non-blank, not merely a str. "" passes isinstance and then renders an
        # empty cell, which is a partial row wearing the no-signal rule's badge
        # -- the same required-field failure as the key being absent. Codex
        # caught this on `id` (#215); `name` had it too and was not flagged, so
        # it is fixed here as the boundary rather than the line.
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a dependency record has no usable name; the audit shape changed")
        # A clean audit carries `vulns: []` on every dependency, so the list
        # being absent is drift, never good news.
        if not isinstance(vulns, list):
            raise ValueError(f"dependency {name} carries no vulns list; the audit shape changed")
        for vuln in vulns:
            advisory_id = vuln.get("id") if isinstance(vuln, dict) else None
            if not isinstance(advisory_id, str) or not advisory_id.strip():
                raise ValueError(f"an advisory on {name} has no usable id; the audit shape changed")
            # Lenient only here, and only because the advisory is still
            # REPORTED: an unreadable fix list costs a cell, not a finding.
            fixes = vuln.get("fix_versions")
            fixes = [f for f in fixes if isinstance(f, str)] if isinstance(fixes, list) else []
            rows.append({
                "package": name,
                "version": version if isinstance(version, str) else "?",
                "id": advisory_id.strip(),
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
