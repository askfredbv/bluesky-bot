"""Tests for the Mastodon tag probe.

The probe's job is to show real tag quality on representative text before the
feature is enabled. Two things have to hold or its output misleads: it must
feed the tagger the same shape production does (root posts only), and its
`raw` and `tags` lines must come from one call — an earlier version made two
independent calls and compared unrelated outputs (Codex review, 2026-09-09).
"""
import json
from types import SimpleNamespace

import pytest

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


# ── post selection ──────────────────────────────────────────────────────────

def test_root_posts_are_returned_in_order(monkeypatch):
    _patch_feed(monkeypatch, _feed({"text": "first"}, {"text": "second"}))
    assert probe._fetch_recent_posts(10) == ["first", "second"]


def test_thread_continuations_are_skipped(monkeypatch):
    """The tagger only ever sees a root post, so the probe must too."""
    _patch_feed(monkeypatch, _feed(
        {"text": "root"},
        {"text": "continuation", "reply": {"parent": {"uri": "at://x"}}},
    ))
    assert probe._fetch_recent_posts(10) == ["root"]


def test_empty_and_missing_text_are_skipped(monkeypatch):
    _patch_feed(monkeypatch, _feed({"text": "   "}, {}, {"text": "real"}))
    assert probe._fetch_recent_posts(10) == ["real"]


def test_malformed_entries_do_not_raise(monkeypatch):
    _patch_feed(monkeypatch, {"feed": [{}, {"post": {}},
                                       {"post": {"record": {"text": "ok"}}}]})
    assert probe._fetch_recent_posts(10) == ["ok"]


# ── one call, not two ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_makes_exactly_one_call_per_post(monkeypatch, capsys):
    """raw and tags must be attributable to the same response, or a difference
    between them proves nothing about the sanitizer."""
    calls = []

    async def one_call(key, post, model, allowance):
        calls.append(post)
        return ["#AI", "#Semiconductors"], ["#Semiconductors"]

    monkeypatch.setattr(probe, "request_mastodon_tags", one_call)
    tagged = await probe._probe_one("key", "model", 1, "a post about chips")

    assert calls == ["a post about chips"]
    assert tagged is True
    out = capsys.readouterr().out
    assert "#AI" in out          # raw shows what the model reached for
    assert "#Semiconductors" in out


@pytest.mark.asyncio
async def test_probe_shows_the_suffix_a_reader_would_see(monkeypatch, capsys):
    async def one_call(key, post, model, allowance):
        return ["#Python"], ["#Python"]

    monkeypatch.setattr(probe, "request_mastodon_tags", one_call)
    await probe._probe_one("key", "model", 1, "a note")
    assert "#Python" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_probe_skips_a_post_that_already_spends_the_ceiling(monkeypatch, capsys):
    called = []

    async def one_call(key, post, model, allowance):
        called.append(1)
        return [], []

    monkeypatch.setattr(probe, "request_mastodon_tags", one_call)
    tagged = await probe._probe_one("key", "model", 1, "a #Python #Linux post")

    assert called == []
    assert tagged is False
    assert "ceiling" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_failing_call_does_not_stop_the_run(monkeypatch, capsys):
    async def boom(key, post, model, allowance):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(probe, "request_mastodon_tags", boom)
    assert await probe._probe_one("key", "model", 1, "a note") is False
    assert "FAILED" in capsys.readouterr().out


# ── fallbacks ───────────────────────────────────────────────────────────────

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


@pytest.mark.asyncio
async def test_run_reports_a_missing_key_rather_than_calling(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert await probe._run(["a note"]) == 1
