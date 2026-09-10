"""Regression guard for ledger X7: the proactive scan and the approval must
never run at the same time.

Both read pending_replies.json from the state Gist and write the whole file back
with no compare-and-swap. They used to sit in different concurrency groups
(proactive-scan-<ref> / proactive-approve-<ref>), so GitHub would run them
together, and a scan that loaded state before an approval finished could write
it back and resurrect the approved draft. One shared group serialises them.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"
_WRITERS = ("proactive_scan.yml", "approve_pending_reply.yml")


def _concurrency(name: str) -> dict:
    """The top-level ``concurrency:`` block of a workflow, as a flat dict."""
    lines = (_WORKFLOWS / name).read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "concurrency:" and not line.startswith(" "))
    block = {}
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped or not line.startswith(" "):
            break
        if stripped.startswith("#"):
            continue
        key, _, value = stripped.partition(":")
        block[key.strip()] = value.strip()
    return block


def test_both_writers_share_one_concurrency_group():
    groups = {name: _concurrency(name).get("group") for name in _WRITERS}
    assert all(groups.values()), f"a writer has no concurrency group: {groups}"
    assert len(set(groups.values())) == 1, (
        f"the writers are in different groups, so GitHub can run them concurrently: {groups}")


def test_the_group_is_not_scoped_per_ref():
    """pending_replies.json is one global file in the Gist. A per-ref group would
    still let a run dispatched from another branch race the one on main."""
    for name in _WRITERS:
        assert "github.ref" not in _concurrency(name)["group"], name


def test_a_running_write_is_never_cancelled():
    """Cancelling an approval mid-run could post a reply and never record it."""
    for name in _WRITERS:
        assert _concurrency(name).get("cancel-in-progress") == "false", name


def test_only_these_two_workflows_write_the_file():
    """If anything else starts writing pending_replies.json, its workflow has to
    join the same group. Today only these two scripts save it."""
    writers = sorted(
        str(path.relative_to(_ROOT)).replace(chr(92), "/")
        for path in (_ROOT / "scripts").glob("*.py")
        if "save_pending_replies(" in path.read_text(encoding="utf-8")
    )
    assert writers == ["scripts/run_proactive_approve.py", "scripts/run_proactive_scan.py"], (
        "a new script writes pending_replies.json; its workflow must join the shared concurrency group")
    texts = {name: (_WORKFLOWS / name).read_text(encoding="utf-8") for name in _WRITERS}
    assert "scripts.run_proactive_scan" in texts["proactive_scan.yml"]
    assert "scripts.run_proactive_approve" in texts["approve_pending_reply.yml"]
