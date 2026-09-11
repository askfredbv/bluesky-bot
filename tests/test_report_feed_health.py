"""scripts/report_feed_health.py: one GitHub issue for feeds that look dead."""
import json

import pytest

from scripts import report_feed_health as rfh

BROKEN = {"url": "https://dead.example/feed", "reason": "broken", "detail": "…",
          "last_ok_at": "2026-09-04T07:00:00+00:00", "last_accepted_at": None}
STALE = {"url": "https://quiet.example/rss", "reason": "stale", "detail": "…",
         "last_ok_at": "2026-09-11T07:00:00+00:00", "last_accepted_at": "2026-08-20T07:00:00+00:00"}


class FakeGh:
    """Records every `gh` call; answers `issue list` with the open issue, if any."""

    def __init__(self, open_issue=None):
        self.calls = []
        self.open_issue = open_issue

    def __call__(self, args):
        self.calls.append(list(args))
        if list(args[:2]) == ["issue", "list"]:
            return json.dumps([self.open_issue] if self.open_issue else [])
        return ""

    def verbs(self):
        return [c[:2] for c in self.calls]


# --- the body --------------------------------------------------------------

def test_body_lists_each_flagged_feed_with_a_date_not_an_age():
    body = rfh.render_body([STALE, BROKEN])
    assert "| https://dead.example/feed | broken | no successful fetch since 2026-09-04 |" in body
    assert "| https://quiet.example/rss | stale | no usable entry since 2026-08-20 |" in body
    assert rfh.flagged_urls_in(body) == ["https://dead.example/feed", "https://quiet.example/rss"]


def test_a_feed_that_never_worked_says_so():
    assert "never fetched successfully" in rfh.render_body([{**BROKEN, "last_ok_at": None}])
    assert "never produced a usable entry" in rfh.render_body([{**STALE, "last_accepted_at": None}])


# --- the decision ----------------------------------------------------------

def test_plan_opens_an_issue_when_feeds_are_flagged_and_none_is_open():
    assert rfh.plan([BROKEN], None) == "open"


def test_plan_does_nothing_when_the_open_issue_already_says_the_same():
    """Twice a day with nothing new must not edit the issue."""
    assert rfh.plan([BROKEN], {"number": 7, "body": rfh.render_body([BROKEN])}) == "nothing"


def test_plan_updates_and_comments_when_the_set_of_feeds_changes():
    assert rfh.plan([BROKEN, STALE], {"number": 7, "body": rfh.render_body([BROKEN])}) == "update+comment"


def test_plan_updates_quietly_when_only_a_date_changes():
    later = {**BROKEN, "last_ok_at": "2026-09-01T07:00:00+00:00"}
    assert rfh.plan([later], {"number": 7, "body": rfh.render_body([BROKEN])}) == "update"


def test_plan_closes_the_issue_once_nothing_is_flagged():
    assert rfh.plan([], {"number": 7, "body": rfh.render_body([BROKEN])}) == "close"


def test_plan_does_nothing_when_all_is_well_and_no_issue_is_open():
    assert rfh.plan([], None) == "nothing"


# --- applying it through gh ------------------------------------------------

def test_report_opens_a_labelled_issue_listing_the_feed():
    gh = FakeGh()
    assert rfh.report([BROKEN], gh) == "open"
    create = next(c for c in gh.calls if c[:2] == ["issue", "create"])
    assert create[create.index("--label") + 1] == rfh.LABEL
    assert "https://dead.example/feed" in create[create.index("--body") + 1]
    assert ["label", "create"] in gh.verbs(), "the label must exist before the issue uses it"


def test_report_rewrites_and_comments_when_the_set_changes():
    gh = FakeGh(open_issue={"number": 7, "body": rfh.render_body([BROKEN])})
    assert rfh.report([BROKEN, STALE], gh) == "update+comment"
    assert ["issue", "edit", "7"] in [c[:3] for c in gh.calls]
    assert ["issue", "comment", "7"] in [c[:3] for c in gh.calls]


def test_report_leaves_an_up_to_date_issue_alone():
    gh = FakeGh(open_issue={"number": 7, "body": rfh.render_body([BROKEN])})
    assert rfh.report([BROKEN], gh) == "nothing"
    assert not {("issue", "edit"), ("issue", "comment"), ("issue", "create"), ("issue", "close")} & {
        tuple(v) for v in gh.verbs()
    }


def test_report_closes_the_issue_when_every_feed_recovers():
    gh = FakeGh(open_issue={"number": 7, "body": rfh.render_body([BROKEN])})
    assert rfh.report([], gh) == "close"
    assert ["issue", "close", "7"] in [c[:3] for c in gh.calls]


# --- main: never act on a state it could not read ----------------------------

@pytest.mark.parametrize("read", [(None, False), (None, True), ({"no": "feeds"}, True)])
def test_main_never_touches_the_issue_without_trustworthy_feed_health(monkeypatch, read):
    """A failed Gist read must not look like "every feed is healthy" and close
    a real issue: the same trap the state trust guards exist for."""
    monkeypatch.setattr(rfh, "_load_gist_state_strict", lambda _f: read)
    reported = []
    monkeypatch.setattr(rfh, "report", lambda alerts, gh=None: reported.append(alerts) or "nothing")

    assert rfh.main() == 0
    assert reported == []


def test_main_reports_what_feed_health_alerts_flags_for_the_configured_feeds(monkeypatch):
    health = {"feeds": {}}
    monkeypatch.setattr(rfh, "_load_gist_state_strict", lambda _f: (health, True))
    monkeypatch.setattr(rfh, "feed_health_alerts",
                        lambda h, feeds: [BROKEN] if h is health and feeds is rfh.RSS_FEEDS else [])
    reported = []
    monkeypatch.setattr(rfh, "report", lambda alerts, gh=None: reported.append(alerts) or "open")

    assert rfh.main() == 0
    assert reported == [[BROKEN]]
