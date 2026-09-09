"""Tests for the Mastodon tag probe's post selection.

The probe's job is to show tag quality on representative text. If it fed the
tagger thread continuations or empty records, the output would misrepresent
what the bot actually tags (the tagger only ever sees a root post).
"""
import json
from types import SimpleNamespace

from scripts import probe_mastodon_tags as probe


class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _feed(*records):
    return {"feed": [{"post": {"record": r}} for r in records]}


def _patch_feed(monkeypatch, payload):
    monkeypatch.setattr(
        probe.urllib.request, "urlopen", lambda *a, **kw: _Resp(payload)
    )


def test_root_posts_are_returned_in_order(monkeypatch):
    _patch_feed(monkeypatch, _feed(
        {"text": "first"}, {"text": "second"},
    ))
    assert probe._fetch_recent_posts(10) == ["first", "second"]


def test_thread_continuations_are_skipped(monkeypatch):
    """The tagger only ever sees a root post, so the probe must too."""
    _patch_feed(monkeypatch, _feed(
        {"text": "root"},
        {"text": "continuation", "reply": {"parent": {"uri": "at://x"}}},
    ))
    assert probe._fetch_recent_posts(10) == ["root"]


def test_empty_and_missing_text_are_skipped(monkeypatch):
    _patch_feed(monkeypatch, _feed(
        {"text": "   "}, {}, {"text": "real"},
    ))
    assert probe._fetch_recent_posts(10) == ["real"]


def test_malformed_entries_do_not_raise(monkeypatch):
    monkeypatch.setattr(
        probe.urllib.request, "urlopen",
        lambda *a, **kw: _Resp({"feed": [{}, {"post": {}}, {"post": {"record": {"text": "ok"}}}]}),
    )
    assert probe._fetch_recent_posts(10) == ["ok"]


def test_main_falls_back_to_samples_when_the_feed_fails(monkeypatch, capsys):
    """A probe that dies on a feed outage tells us nothing about the prompt."""
    def boom(*a, **kw):
        raise OSError("no network")

    monkeypatch.setattr(probe.urllib.request, "urlopen", boom)
    monkeypatch.setattr(probe, "_run", lambda posts: SimpleNamespace(posts=posts))
    monkeypatch.setattr(probe.asyncio, "run", lambda coro: len(coro.posts))

    assert probe.main() == len(probe._FALLBACK_POSTS)
    assert "built-in samples" in capsys.readouterr().out


def test_main_falls_back_when_the_feed_is_empty(monkeypatch, capsys):
    _patch_feed(monkeypatch, {"feed": []})
    monkeypatch.setattr(probe, "_run", lambda posts: SimpleNamespace(posts=posts))
    monkeypatch.setattr(probe.asyncio, "run", lambda coro: len(coro.posts))

    assert probe.main() == len(probe._FALLBACK_POSTS)
    assert "built-in samples" in capsys.readouterr().out
