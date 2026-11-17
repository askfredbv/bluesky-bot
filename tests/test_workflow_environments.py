"""Every job that reads a repository secret runs in the main-only `production`
environment, so a workflow dispatched from another branch cannot reach them.
Text-based on purpose: no YAML parser in the test dependencies."""
import re
from pathlib import Path

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"
_SECRET = re.compile(r"secrets\.(?!GITHUB_TOKEN\b)[A-Z_]+")


def _jobs(text: str):
    body = text[text.index("\njobs:\n"):]
    parts = re.split(r"\n  ([A-Za-z0-9_-]+):\n", body)
    return dict(zip(parts[1::2], parts[2::2]))


def test_secret_using_jobs_run_in_the_production_environment():
    missing = [
        f"{wf.name}:{job}"
        for wf in sorted(WORKFLOWS.glob("*.yml"))
        for job, block in _jobs(wf.read_text(encoding="utf-8")).items()
        if _SECRET.search(block) and "\n    environment: production" not in "\n" + block
    ]
    assert not missing, missing


def test_the_check_actually_sees_the_secret_jobs():
    seen = [
        job
        for wf in WORKFLOWS.glob("*.yml")
        for job, block in _jobs(wf.read_text(encoding="utf-8")).items()
        if _SECRET.search(block)
    ]
    assert len(seen) >= 10, seen
