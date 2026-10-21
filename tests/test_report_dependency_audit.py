"""scripts/report_dependency_audit.py: one GitHub issue for lockfile advisories."""
import json

import pytest

from scripts import report_dependency_audit as rda

# The shape pip-audit 2.10.1 really emits, trimmed to the fields used.
AUDIT = {
    "dependencies": [
        {"name": "pillow", "version": "12.3.0", "vulns": [
            {"id": "CVE-2026-112895", "fix_versions": ["13.0.0"], "aliases": ["GHSA-p4wg"]},
            {"id": "CVE-2026-112400", "fix_versions": ["13.0.0"], "aliases": []},
        ]},
        {"name": "certifi", "version": "2026.7.22", "vulns": [
            {"id": "PYSEC-2026-4320", "fix_versions": ["2026.10.19"], "aliases": []},
        ]},
    ],
    "fixes": [],
}

PILLOW = {"package": "pillow", "version": "12.3.0", "id": "CVE-2026-112895", "fix": "13.0.0"}
CERTIFI = {"package": "certifi", "version": "2026.7.22", "id": "PYSEC-2026-4320", "fix": "2026.10.19"}


class FakeGh:
    """Records every `gh` call; answers `issue list` with the open issue, if any."""

    def __init__(self, open_issue=None, open_issues=None):
        self.calls = []
        if open_issues is not None:
            self.open_issues = list(open_issues)
        else:
            self.open_issues = [open_issue] if open_issue else []

    def __call__(self, args):
        self.calls.append(list(args))
        if list(args[:2]) == ["issue", "list"]:
            return json.dumps(self.open_issues)
        return ""

    def closed(self):
        return [c[2] for c in self.calls if c[:2] == ["issue", "close"]]

    def verbs(self):
        return [c[:2] for c in self.calls]


# --- reading the audit -----------------------------------------------------

def test_the_real_pip_audit_shape_flattens_to_one_row_per_advisory():
    rows = rda.parse_audit(AUDIT)
    assert len(rows) == 3
    assert PILLOW in rows and CERTIFI in rows


def test_a_clean_audit_is_no_findings_not_an_error():
    assert rda.parse_audit({"dependencies": [{"name": "httpx", "version": "0.28.1", "vulns": []}]}) == []


@pytest.mark.parametrize("payload", [
    [],                                  # a list, not an object
    "nope",                              # not JSON at all, structurally
    {},                                  # no dependencies key
    {"dependencies": {"pillow": []}},    # dependencies is not a list
])
def test_a_shape_it_does_not_recognise_raises_rather_than_reading_as_clean(payload):
    """The whole point: "I could not read the audit" must not become "clean"."""
    with pytest.raises(ValueError):
        rda.parse_audit(payload)


@pytest.mark.parametrize("deps", [
    ["junk"],                                                   # not an object
    [{"version": "1.0", "vulns": []}],                          # no name
    [{"name": "pillow", "version": "12.3.0"}],                  # no vulns list
    [{"name": "pillow", "version": "12.3.0", "vulns": {}}],     # vulns not a list
    [{"name": "pillow", "version": "12.3.0", "vulns": [{"no_id": True}]}],
    # Blank is the same required-field failure as absent: "" passes isinstance
    # and then renders an empty cell (Codex, #215). `name` had the same hole.
    [{"name": "pillow", "version": "12.3.0", "vulns": [{"id": ""}]}],
    [{"name": "pillow", "version": "12.3.0", "vulns": [{"id": "   "}]}],
    [{"name": "", "version": "12.3.0", "vulns": []}],
    [{"name": "   ", "version": "12.3.0", "vulns": []}],
], ids=["not-an-object", "no-name", "no-vulns", "vulns-not-a-list", "advisory-without-id",
        "blank-id", "whitespace-id", "blank-name", "whitespace-name"])
def test_an_unreadable_record_means_no_signal_not_a_partial_report(deps):
    """Earlier versions SKIPPED these, which turns a partial report into an
    apparently complete one -- and an empty report closes the issue. For an
    alert whose failure mode is silent closure, under-reporting is the
    expensive direction."""
    with pytest.raises(ValueError):
        rda.parse_audit({"dependencies": deps})


def test_a_readable_record_next_to_an_unreadable_one_does_not_rescue_it():
    """The `any` hole, named: one clean `vulns: []` record must not vouch for a
    payload whose other record carries a live CVE under a renamed key."""
    with pytest.raises(ValueError):
        rda.parse_audit({"dependencies": [
            {"name": "httpx", "version": "0.28.1", "vulns": []},
            {"name": "pillow", "version": "12.3.0", "advisories": [{"id": "CVE-2026-112895"}]},
        ]})


def test_an_unreadable_fix_list_still_reports_the_advisory():
    """Lenient only where the advisory survives: a bad fix list costs a cell."""
    rows = rda.parse_audit({"dependencies": [
        {"name": "pillow", "version": "12.3.0", "vulns": [{"id": "CVE-1", "fix_versions": "13.0.0"}]},
    ]})
    assert rows == [{"package": "pillow", "version": "12.3.0", "id": "CVE-1", "fix": "no fix released"}]


def test_an_advisory_with_no_fix_says_so_rather_than_showing_an_empty_cell():
    rows = rda.parse_audit({"dependencies": [
        {"name": "lonely", "version": "1.0", "vulns": [{"id": "PYSEC-9", "fix_versions": []}]},
    ]})
    assert rows[0]["fix"] == "no fix released"
    assert "no fix released" in rda.render_body(rows)


# --- the body --------------------------------------------------------------

def test_body_lists_each_advisory_with_its_fix_version():
    body = rda.render_body([CERTIFI, PILLOW])
    assert "| pillow | 12.3.0 | CVE-2026-112895 | 13.0.0 |" in body
    assert "| certifi | 2026.7.22 | PYSEC-2026-4320 | 2026.10.19 |" in body
    assert rda.advisory_ids_in(body) == ["CVE-2026-112895", "PYSEC-2026-4320"]


def test_the_body_warns_that_a_lockfile_refresh_can_report_success_and_fix_nothing():
    """The Pillow <13 trap: the fix version sat outside the declared range."""
    body = rda.render_body([PILLOW])
    assert "pyproject.toml" in body
    assert "closed nothing" in body


def test_advisory_ids_ignores_the_header_and_separator_rows():
    assert rda.advisory_ids_in(rda.render_body([])) == []


# --- the lifecycle ---------------------------------------------------------

def test_advisories_and_no_issue_opens_one():
    assert rda.plan([PILLOW], None) == "open"


def test_nothing_found_and_no_issue_does_nothing():
    assert rda.plan([], None) == "nothing"


def test_nothing_found_with_an_issue_open_closes_it():
    assert rda.plan([], {"number": 7, "body": rda.render_body([PILLOW])}) == "close"


def test_the_same_advisories_on_the_next_daily_run_change_nothing():
    """It runs every day now, so an unchanged report must not comment."""
    issue = {"number": 7, "body": rda.render_body([PILLOW, CERTIFI])}
    assert rda.plan([CERTIFI, PILLOW], issue) == "nothing"


def test_a_changed_set_of_advisories_comments_as_well_as_updates():
    issue = {"number": 7, "body": rda.render_body([PILLOW])}
    assert rda.plan([PILLOW, CERTIFI], issue) == "update+comment"


def test_the_same_advisory_with_a_new_fix_version_updates_without_commenting():
    issue = {"number": 7, "body": rda.render_body([dict(PILLOW, fix="no fix released")])}
    assert rda.plan([PILLOW], issue) == "update"


def test_report_opens_the_issue_and_ensures_the_label_exists():
    gh = FakeGh()
    assert rda.report([PILLOW], gh) == "open"
    assert ["label", "create"] in gh.verbs()
    assert ["issue", "create"] in gh.verbs()


def test_report_closes_the_issue_when_the_lockfile_comes_back_clean():
    gh = FakeGh(open_issue={"number": 7, "body": rda.render_body([PILLOW])})
    assert rda.report([], gh) == "close"
    assert ["issue", "close"] in gh.verbs()


def test_report_never_opens_a_second_issue_while_one_is_open():
    gh = FakeGh(open_issue={"number": 7, "body": "stale body"})
    rda.report([PILLOW], gh)
    assert ["issue", "create"] not in gh.verbs()
    assert ["issue", "edit"] in gh.verbs()


# --- the failure path that matters ----------------------------------------

def test_an_unreadable_audit_file_leaves_the_issue_completely_alone(tmp_path, monkeypatch, capsys):
    """A missing audit must not close a real issue. Same rule as #200's Gist read."""
    called = []
    monkeypatch.setattr(rda, "report", lambda *a, **k: called.append(a) or "open")
    assert rda.main([str(tmp_path / "never-written.json")]) == 0
    assert called == []
    assert "leaving the issue alone" in capsys.readouterr().out


def test_a_malformed_audit_file_leaves_the_issue_completely_alone(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "audit.json"
    bad.write_text('{"dependencies": "not a list"}', encoding="utf-8")
    called = []
    monkeypatch.setattr(rda, "report", lambda *a, **k: called.append(a) or "open")
    assert rda.main([str(bad)]) == 0
    assert called == []
    assert "leaving the issue alone" in capsys.readouterr().out


def test_a_readable_audit_reaches_the_report(tmp_path, monkeypatch):
    good = tmp_path / "audit.json"
    good.write_text(json.dumps(AUDIT), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(rda, "report", lambda findings: seen.setdefault("n", len(findings)) or "open")
    assert rda.main([str(good)]) == 0
    assert seen["n"] == 3


# --- what Codex caught on #215 --------------------------------------------

def test_a_shape_that_still_parses_but_yields_nothing_raises_instead_of_reading_as_clean():
    """The dangerous drift: not a payload we reject, one that quietly means
    "clean" and closes a real issue. A clean audit says vulns: [] per dep, so
    the key missing everywhere is drift."""
    with pytest.raises(ValueError):
        rda.parse_audit({"dependencies": [
            {"name": "pillow", "version": "12.3.0", "advisories": [{"id": "CVE-1"}]},
            {"name": "certifi", "version": "2026.7.22", "advisories": []},
        ]})


def test_a_genuinely_clean_audit_is_still_clean_not_drift():
    """vulns present and empty is the real clean signal and must survive."""
    assert rda.parse_audit({"dependencies": [
        {"name": "httpx", "version": "0.28.1", "vulns": []},
        {"name": "pillow", "version": "13.0.0", "vulns": []},
    ]}) == []


def test_an_empty_dependency_list_is_not_treated_as_drift():
    assert rda.parse_audit({"dependencies": []}) == []


def test_a_raced_duplicate_issue_is_folded_into_the_oldest_one():
    """Two reporters can both open one; a limit of 1 would leave the second open
    forever, including after the lockfile came back clean."""
    gh = FakeGh(open_issues=[
        {"number": 31, "body": "second, from the race"},
        {"number": 30, "body": "first"},
    ])
    rda.report([PILLOW], gh)
    assert gh.closed() == ["31"]
    assert ["issue", "edit"] in gh.verbs()
    assert "30" in [c[2] for c in gh.calls if c[:2] == ["issue", "edit"]]


def test_duplicates_are_reconciled_even_when_the_lockfile_is_clean():
    gh = FakeGh(open_issues=[{"number": 31, "body": "dup"}, {"number": 30, "body": "first"}])
    assert rda.report([], gh) == "close"
    assert sorted(gh.closed()) == ["30", "31"]


def test_one_open_issue_is_not_mistaken_for_a_duplicate():
    gh = FakeGh(open_issue={"number": 30, "body": "only one"})
    rda.report([PILLOW], gh)
    assert gh.closed() == []


def test_a_blank_id_publishes_a_row_that_identifies_nothing():
    """Why the blank must be refused upstream: the row renders with an empty
    Advisory cell, and advisory_ids_in then cannot see it, so the published
    body no longer says what was reported.

    It does NOT nag daily -- plan()'s body-equality check short-circuits before
    the ID comparison. Checked rather than assumed, because the first version of
    this test asserted the nag and was wrong."""
    blank = {"package": "pillow", "version": "12.3.0", "id": "", "fix": "13.0.0"}
    body = rda.render_body([blank])
    assert "| pillow | 12.3.0 |  | 13.0.0 |" in body
    assert rda.advisory_ids_in(body) == []
    assert rda.plan([blank], {"number": 7, "body": body}) == "nothing"
    # which is why parse_audit must never produce such a row in the first place
    with pytest.raises(ValueError):
        rda.parse_audit({"dependencies": [
            {"name": "pillow", "version": "12.3.0", "vulns": [{"id": "", "fix_versions": ["13.0.0"]}]},
        ]})


def test_a_padded_id_is_accepted_but_stored_trimmed():
    rows = rda.parse_audit({"dependencies": [
        {"name": "pillow", "version": "12.3.0", "vulns": [{"id": "  CVE-1  ", "fix_versions": ["13.0.0"]}]},
    ]})
    assert rows[0]["id"] == "CVE-1"
