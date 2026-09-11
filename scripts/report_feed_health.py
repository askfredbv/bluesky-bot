"""Open, update or close one GitHub issue for configured feeds that look dead.

`feed_persistently_unhealthy` used to live only in the run log, and nobody reads
run logs: on 2026-09-10 four dead feeds were found by accident. This script runs
as its own job after the daily post (`feed-health-issue` in daily_post.yml). That
job holds the only token in the workflow that may write issues.

There is one issue, labelled `feed-health`:
  - feeds flagged, no open issue       -> open one listing them
  - feeds flagged, issue already open  -> rewrite its body if it changed, and
                                          comment only when the SET of flagged
                                          feeds changed, so it does not nag
  - nothing flagged, issue open        -> close it with a comment
  - nothing flagged, no issue          -> nothing

The table shows dates (last successful fetch, last usable entry), not ages. So
the body only changes when the facts do, not on every run.

A failed Gist read changes nothing. An unreadable feed_health.json must not look
like "every feed is healthy" and close a real issue.

Run: `python -m scripts.report_feed_health`, with GH_TOKEN (and GH_REPO) for
`gh`, and GIST_ID / GIST_TOKEN for the state Gist.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.config import RSS_FEEDS  # noqa: E402
from src.metrics import feed_health_alerts  # noqa: E402
from src.state_store import _load_gist_state_strict  # noqa: E402

LABEL = "feed-health"
TITLE = "Feed health: configured feeds look dead"
MARKER = "<!-- feed-health-report -->"

Runner = Callable[[Sequence[str]], str]


def _gh(args: Sequence[str]) -> str:
    """Run `gh` and return its stdout; raises if the command fails."""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _day(stamp: Optional[str]) -> Optional[str]:
    return stamp[:10] if stamp else None


def _since(alert: Dict[str, Any]) -> str:
    if alert["reason"] == "broken":
        day = _day(alert.get("last_ok_at"))
        return f"no successful fetch since {day}" if day else "never fetched successfully"
    day = _day(alert.get("last_accepted_at"))
    return f"no usable entry since {day}" if day else "never produced a usable entry"


def render_body(alerts: List[Dict[str, Any]]) -> str:
    lines = [
        MARKER,
        "",
        "The daily run's feed-health check flags these configured feeds:",
        "",
        "| Feed | Signal | Since |",
        "| --- | --- | --- |",
    ]
    for alert in sorted(alerts, key=lambda a: a["url"]):
        lines.append(f"| {alert['url']} | {alert['reason']} | {_since(alert)} |")
    lines += [
        "",
        "**Broken:** no successful fetch for a while (the URL, DNS or the server is failing). "
        "**Stale:** it fetches, but has had nothing usable for a while (a moved feed or a format change).",
        "",
        "Fix or remove the feed in `RSS_FEEDS` (`src/config.py`). This issue closes itself "
        "on the first run where nothing is flagged.",
    ]
    return "\n".join(lines) + "\n"


def flagged_urls_in(body: str) -> List[str]:
    """The feed URLs listed in a report body, for change detection."""
    return sorted(line.split("|")[1].strip() for line in body.splitlines() if line.startswith("| http"))


def plan(alerts: List[Dict[str, Any]], open_issue: Optional[Dict[str, Any]]) -> str:
    """What to do: "open", "update", "update+comment", "close" or "nothing"."""
    if not alerts:
        return "close" if open_issue is not None else "nothing"
    if open_issue is None:
        return "open"
    old_body = open_issue.get("body") or ""
    if old_body.strip() == render_body(alerts).strip():
        return "nothing"
    if flagged_urls_in(old_body) != sorted(alert["url"] for alert in alerts):
        return "update+comment"
    return "update"


def find_open_issue(gh: Runner) -> Optional[Dict[str, Any]]:
    out = gh(["issue", "list", "--label", LABEL, "--state", "open", "--json", "number,body", "--limit", "1"])
    issues = json.loads(out or "[]")
    return issues[0] if issues else None


def report(alerts: List[Dict[str, Any]], gh: Runner = _gh) -> str:
    """Apply ``plan`` through ``gh``; return the action taken."""
    gh(["label", "create", LABEL, "--color", "D93F0B",
        "--description", "A configured RSS feed looks dead", "--force"])
    issue = find_open_issue(gh)
    action = plan(alerts, issue)
    if action == "open":
        gh(["issue", "create", "--title", TITLE, "--label", LABEL, "--body", render_body(alerts)])
    elif action in ("update", "update+comment"):
        assert issue is not None
        number = str(issue["number"])
        gh(["issue", "edit", number, "--body", render_body(alerts)])
        if action == "update+comment":
            now_flagged = ", ".join(sorted(alert["url"] for alert in alerts))
            gh(["issue", "comment", number, "--body", f"The set of flagged feeds changed. Now flagged: {now_flagged}."])
    elif action == "close":
        assert issue is not None
        gh(["issue", "close", str(issue["number"]), "--comment", "Every configured feed is healthy again. Closing."])
    return action


def main() -> int:
    feed_health, trusted = _load_gist_state_strict("feed_health.json")
    if not trusted or not isinstance(feed_health, dict) or "feeds" not in feed_health:
        print("feed-health report: no trustworthy feed_health.json; leaving the issue alone")
        return 0
    alerts = feed_health_alerts(feed_health, RSS_FEEDS)
    action = report(alerts)
    print(f"feed-health report: {len(alerts)} flagged, action={action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
