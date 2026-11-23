"""Security boundary checks for the dependency-audit workflow."""
import re
from pathlib import Path


WORKFLOW = (Path(__file__).parents[1] / ".github" / "workflows"
            / "dependency-audit.yml").read_text(encoding="utf-8")


def _job(name: str, next_name: str | None = None) -> str:
    start = WORKFLOW.index(f"  {name}:")
    end = WORKFLOW.index(f"  {next_name}:", start) if next_name else len(WORKFLOW)
    return WORKFLOW[start:end]


def test_issue_writer_never_installs_or_runs_pip_audit():
    """Unpinned audit transitives must never execute beside the issue-write token."""
    writer = _job("dependency-audit-issue")

    assert "issues: write" in writer
    assert "python -m pip install" not in writer
    assert "python -m pip_audit" not in writer
    assert re.search(r"actions/download-artifact@[0-9a-f]{40}", writer)


def test_read_only_job_produces_the_audit_artifact():
    """The auditor still creates the JSON consumed by the issue reporter."""
    producer = _job("prepare-dependency-audit-report", "dependency-audit-issue")

    assert "permissions:\n      contents: read" in producer
    assert "pip-audit==2.10.1" in producer
    assert "--format json --output audit.json" in producer
    # Pinned by full SHA, not by a specific one: Dependabot moves the SHA.
    assert re.search(r"actions/upload-artifact@[0-9a-f]{40}", producer)


def test_writer_preserves_freshness_and_reporting_path():
    """Only a current main audit may reach the existing issue reporter."""
    writer = _job("dependency-audit-issue")

    assert "AUDITED_REVISION" in writer
    assert "steps.freshness.outputs.current == 'true'" in writer
    assert "GH_TOKEN: ${{ github.token }}" in writer
    assert ('python -m scripts.report_dependency_audit '
            '"$RUNNER_TEMP/dependency-audit/audit.json"') in writer


def test_audit_artifact_cannot_land_in_the_workspace():
    """The artifact comes from a job that ran PyPI code; extracted into the
    workspace it could overwrite the reporter that then runs with GH_TOKEN."""
    writer = _job("dependency-audit-issue")
    download = writer[writer.index("Download dependency audit report"):
                      writer.index("Confirm audit still matches main")]

    assert "path: ${{ runner.temp }}/dependency-audit" in download
    assert "github.workspace" not in download
