"""Tests for the image contact sheet.

Two things here are load-bearing rather than incidental. First, the split
between a generated illustration and a link-card thumbnail: the public feed
cannot say whether a card's thumb is the article's own or one the bot made, so
the report must not claim to know. Second, every item must render as an actual
image — the whole point of the issue is that a human looks at pictures, and a
summary or a table in its place would be the failure mode the corrective
exists to prevent.
"""
import httpx
import pytest

from scripts import run_image_audit as audit


def _resp(**kwargs):
    """An httpx.Response with a request attached, so raise_for_status() works.

    A bare Response(200) raises RuntimeError from raise_for_status because the
    request was never set — which would make these tests pass for the wrong
    reason (via the except branch) rather than exercise the success path.
    """
    return httpx.Response(request=httpx.Request("GET", "https://cdn.bsky.app/x"),
                          **kwargs)


def _image_post(uri="at://did:plc:abc/app.bsky.feed.post/aaa", urls=("https://cdn.bsky.app/img/feed_fullsize/plain/did/one",)):
    return {"post": {
        "uri": uri,
        "record": {"text": "A decision looks technical.", "createdAt": "2026-10-19T14:34:48.109Z"},
        "embed": {
            "$type": "app.bsky.embed.images#view",
            "images": [{"fullsize": u, "alt": "Illustration"} for u in urls],
        },
    }}


def _card_post(thumb="https://cdn.bsky.app/img/feed_thumbnail/plain/did/two",
               article="https://example.com/a"):
    embed = {"$type": "app.bsky.embed.external#view",
             "external": {"uri": article, "title": "T", "description": "D"}}
    if thumb:
        embed["external"]["thumb"] = thumb
    return {"post": {
        "uri": "at://did:plc:abc/app.bsky.feed.post/bbb",
        "record": {"text": "Curator text", "createdAt": "2026-10-18T07:01:00.000Z"},
        "embed": embed,
    }}


# --- URL shaping -----------------------------------------------------------

def test_post_web_url_from_at_uri():
    url = audit._post_web_url("at://did:plc:abc/app.bsky.feed.post/3kxyz", "askfred.be")
    assert url == "https://bsky.app/profile/askfred.be/post/3kxyz"


@pytest.mark.parametrize("bad", ["", "at://did:plc:abc/app.bsky.feed.like/3k",
                                 "at://did:plc:abc/app.bsky.feed.post/", "nonsense"])
def test_post_web_url_rejects_anything_not_a_post(bad):
    """A malformed record costs a missing link, never the report."""
    assert audit._post_web_url(bad, "askfred.be") == ""


def test_preview_flattens_and_caps():
    long_text = "word " * 100
    out = audit._preview(long_text)
    assert "\n" not in out
    assert len(out) <= audit._TEXT_PREVIEW_CHARS
    assert out.endswith("…")


# --- classification --------------------------------------------------------

def test_illustrations_and_cards_are_separated():
    illustrations, cards = audit.classify_feed(
        [_image_post(), _card_post()], "askfred.be")
    assert len(illustrations) == 1
    assert len(cards) == 1
    assert illustrations[0].source_uri is None
    assert cards[0].source_uri == "https://example.com/a"


def test_every_image_in_a_multi_image_embed_is_collected():
    illustrations, _ = audit.classify_feed(
        [_image_post(urls=("https://cdn.bsky.app/a", "https://cdn.bsky.app/b"))],
        "askfred.be")
    assert [i.image_url for i in illustrations] == [
        "https://cdn.bsky.app/a", "https://cdn.bsky.app/b"]


def test_card_without_a_thumb_is_not_an_item():
    """2026-10-09 posted a card with no thumbnail at all — nothing to look at."""
    illustrations, cards = audit.classify_feed([_card_post(thumb=None)], "askfred.be")
    assert (illustrations, cards) == ([], [])


def test_reposts_are_skipped():
    """A repost's picture is somebody else's work and not under our prompt."""
    item = _image_post()
    item["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
    assert audit.classify_feed([item], "askfred.be") == ([], [])


@pytest.mark.parametrize("post", [
    {"post": {"uri": "at://x/app.bsky.feed.post/1", "record": {"text": "no embed"}}},
    {"post": {"uri": "at://x/app.bsky.feed.post/1", "record": {}, "embed": {}}},
    {},
])
def test_posts_without_pictures_are_ignored(post):
    assert audit.classify_feed([post], "askfred.be") == ([], [])


def test_empty_feed_is_not_an_error():
    assert audit.classify_feed([], "askfred.be") == ([], [])
    assert audit.classify_feed(None, "askfred.be") == ([], [])


# --- the report ------------------------------------------------------------

def test_report_renders_one_image_per_item():
    """The constraint: the output is the pictures, not a description of them.

    If this ever fails because the report became a table or a summary, the
    corrective has been undone — see docs/BACKLOG.md 2026-10-19.
    """
    illustrations, cards = audit.classify_feed(
        [_image_post(), _card_post()], "askfred.be")
    report = audit.render_report(illustrations, cards, posts_scanned=2)
    assert report.count("![](") == 2
    for item in illustrations + cards:
        assert f"![]({item.image_url})" in report


def test_report_states_the_question_and_the_counts():
    illustrations, cards = audit.classify_feed(
        [_image_post(), _card_post()], "askfred.be")
    report = audit.render_report(illustrations, cards, posts_scanned=2)
    assert "would you have posted this picture?" in report
    assert "1 generated illustrations and 1 link-card thumbnails" in report
    # The ambiguity is disclosed, not papered over.
    assert "does not say" in report


def test_report_links_the_post_and_quotes_the_text():
    illustrations, _ = audit.classify_feed([_image_post()], "askfred.be")
    report = audit.render_report(illustrations, [], posts_scanned=1)
    assert "https://bsky.app/profile/askfred.be/post/aaa" in report
    assert "> A decision looks technical." in report
    assert "2026-10-19 14:34 UTC" in report


def test_report_with_no_pictures_says_that_is_worth_a_look():
    report = audit.render_report([], [], posts_scanned=12)
    assert "No pictures in the window" in report
    assert "![](" not in report


def test_report_omits_a_section_that_has_nothing_in_it():
    illustrations, _ = audit.classify_feed([_image_post()], "askfred.be")
    report = audit.render_report(illustrations, [], posts_scanned=1)
    assert "Generated illustrations (1)" in report
    assert "Link-card thumbnails" not in report


# --- fetching --------------------------------------------------------------

def test_fetch_clamps_the_limit_to_what_the_endpoint_allows(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen.update(params or {})
        return _resp(status_code=200, json={"feed": [_image_post()]})

    monkeypatch.setattr(audit.httpx, "get", fake_get)
    feed = audit.fetch_feed("askfred.be", 5000)
    assert seen["limit"] == audit._MAX_LIMIT
    assert seen["filter"] == "posts_no_replies"
    assert len(feed) == 1


def test_fetch_tolerates_a_feed_that_is_not_a_list(monkeypatch):
    monkeypatch.setattr(audit.httpx, "get",
                        lambda *a, **k: _resp(status_code=200, json={"feed": "weird"}))
    assert audit.fetch_feed("askfred.be", 10) == []


# --- saving the files ------------------------------------------------------

def test_save_images_refuses_a_host_that_is_not_the_bluesky_cdn(tmp_path, monkeypatch):
    """Exact-host allowlist. The URLs come from the AppView, but a fetch driven
    by a value out of an API response gets an allowlist, not trust."""
    called = []
    monkeypatch.setattr(audit.httpx, "get",
                        lambda *a, **k: called.append(a) or _resp(status_code=200))
    item = audit.AuditItem(created_at="2026-10-19T00:00:00Z", text="t",
                           image_url="https://evil.example.com/img.jpg", post_url="")
    assert audit.save_images([item], tmp_path) == 0
    assert called == []


def test_save_images_writes_the_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(audit.httpx, "get",
                        lambda *a, **k: _resp(status_code=200, content=b"\x89PNG\r\n\x1a\n"))
    item = audit.AuditItem(created_at="2026-10-19T14:34:48Z", text="t",
                           image_url="https://cdn.bsky.app/img/feed_fullsize/plain/d/c",
                           post_url="")
    assert audit.save_images([item], tmp_path / "out") == 1
    written = list((tmp_path / "out").iterdir())
    assert len(written) == 1
    assert written[0].name == "01_2026-10-19.jpg"
    assert written[0].read_bytes().startswith(b"\x89PNG")


def test_save_images_survives_a_failed_download(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(audit.httpx, "get", boom)
    item = audit.AuditItem(created_at="2026-10-19T00:00:00Z", text="t",
                           image_url="https://cdn.bsky.app/img/x", post_url="")
    assert audit.save_images([item], tmp_path) == 0


# --- the entry point -------------------------------------------------------

def test_main_prints_the_sheet(monkeypatch, capsys):
    monkeypatch.setattr(audit, "fetch_feed", lambda a, limit: [_image_post()])
    assert audit.main(["--limit", "10"]) == 0
    out = capsys.readouterr().out
    assert "![](" in out
    assert "look at them" in out


def test_main_reports_a_dead_feed_without_failing(monkeypatch, capsys):
    """Best-effort: a flaky AppView should not page anyone, but it must also
    not print an empty sheet that reads as 'the images are fine'."""
    def boom(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(audit, "fetch_feed", boom)
    assert audit.main([]) == 0
    out = capsys.readouterr().out
    assert "could not read the feed" in out
    assert "ReadTimeout" in out
    assert "Nothing is known about the images either way." in out


def test_main_saves_images_when_asked(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(audit, "fetch_feed", lambda a, limit: [_image_post()])
    monkeypatch.setattr(audit.httpx, "get",
                        lambda *a, **k: _resp(status_code=200, content=b"bytes"))
    assert audit.main(["--out", str(tmp_path / "imgs")]) == 0
    assert len(list((tmp_path / "imgs").iterdir())) == 1
    # The save log goes to stderr so it cannot land in the issue body.
    captured = capsys.readouterr()
    assert "saved 1 image" in captured.err
    assert "saved 1 image" not in captured.out
