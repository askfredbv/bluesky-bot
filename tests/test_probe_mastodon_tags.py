"""Tests for the Mastodon tag probe.

The probe's job is to show real tag quality on representative text before the
feature is enabled. Two things have to hold or its output misleads: it must
feed the tagger the same shape production does (root posts only), and its
`raw` and `sanitized` lines must come from one call — an earlier version made
two independent calls and compared unrelated outputs.

The controlled cases carry their own expectations in both directions, and a
verdict the parser refuses is counted as INVALID rather than as a rejection,
so neither a reviewer that drops everything nor one returning junk can score
well (Codex reviews, 2026-09-09).
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


# ── attributing a lost tag to the right gate ───────────────────────────────

def _patch_pipeline(monkeypatch, raw, candidates, kept):
    proposals, reviews = [], []

    async def propose(key, post, model, allowance):
        proposals.append(post)
        return raw, candidates

    async def review(key, post, cands, model):
        reviews.append(cands)
        return [c in kept for c in cands], kept

    monkeypatch.setattr(probe, "request_mastodon_tags", propose)
    monkeypatch.setattr(probe, "review_mastodon_tags", review)
    return proposals, reviews


@pytest.mark.asyncio
async def test_probe_shows_all_three_stages(monkeypatch, capsys):
    """raw and sanitized come from one propose call so their difference is
    attributable to the sanitizer; reviewed shows what the semantic pass
    dropped. Without all three, a lost tag cannot be blamed on the right gate."""
    proposals, reviews = _patch_pipeline(
        monkeypatch, ["#AI", "#Semiconductors", "#NVIDIA"],
        ["#Semiconductors", "#NVIDIA"], ["#Semiconductors"],
    )
    tagged = await probe._probe_one("key", "proposer", "reviewer", 1, "a post about chips")

    assert proposals == ["a post about chips"]
    assert reviews == [["#Semiconductors", "#NVIDIA"]]
    assert tagged is True
    out = capsys.readouterr().out
    assert "#AI" in out                  # raw: what the model reached for
    assert "sanitized" in out            # what the lexical gate allowed
    assert "dropped: #NVIDIA" in out     # what the semantic gate refused


@pytest.mark.asyncio
async def test_probe_shows_the_suffix_a_reader_would_see(monkeypatch, capsys):
    """Asserts the `ends` line specifically. Checking only that the tag appears
    somewhere in the output would pass with suffix reporting deleted, since the
    raw/sanitized/reviewed lines already contain it (Codex review, 2026-09-09).
    """
    _patch_pipeline(monkeypatch, ["#Python"], ["#Python"], ["#Python"])
    await probe._probe_one("key", "proposer", "reviewer", 1, "a note")
    ends = [ln for ln in capsys.readouterr().out.splitlines() if "ends" in ln]
    assert ends, "no `ends` line was printed"
    assert "\\n\\n#Python" in ends[0]


@pytest.mark.asyncio
async def test_probe_reports_an_invalid_verdict_as_invalid(monkeypatch, capsys):
    """A parser failure on a real post must not be printed as "dropped".

    _probe_adversarial already made this distinction; _probe_one discarded
    the decision status and credited the reviewer with a judgement it never
    made (Codex review, 2026-09-09)."""
    async def propose(key, post, model, allowance):
        return ["#Python"], ["#Python"]

    async def invalid(key, post, cands, model):
        return None, []

    monkeypatch.setattr(probe, "request_mastodon_tags", propose)
    monkeypatch.setattr(probe, "review_mastodon_tags", invalid)
    assert await probe._probe_one("key", "proposer", "reviewer", 1, "a note") is False
    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "dropped" not in out


@pytest.mark.asyncio
async def test_probe_reports_a_review_failure_distinctly(monkeypatch, capsys):
    """A failure in review must not read as 'the model proposed nothing'."""
    async def propose(key, post, model, allowance):
        return ["#Python"], ["#Python"]

    async def boom(key, post, cands, model):
        raise RuntimeError("review exploded")

    monkeypatch.setattr(probe, "request_mastodon_tags", propose)
    monkeypatch.setattr(probe, "review_mastodon_tags", boom)
    assert await probe._probe_one("key", "proposer", "reviewer", 1, "a note") is False
    assert "FAILED (review)" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_reviewer_that_drops_everything_does_not_score_perfectly(
    monkeypatch, capsys
):
    """The positive controls exist for this. Against negatives alone, a
    reviewer that refuses every tag would look flawless."""
    async def drops_everything(key, post, cands, model):
        return [False] * len(cands), []

    monkeypatch.setattr(probe, "review_mastodon_tags", drops_everything)
    correct, invalid, total = await probe._probe_adversarial("key", "model")
    assert invalid == 0
    assert 0 < correct < total, "positives should have been marked wrong"
    assert "WRONG" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_reviewer_that_keeps_everything_is_flagged(monkeypatch, capsys):
    async def keeps_everything(key, post, cands, model):
        return [True] * len(cands), list(cands)

    monkeypatch.setattr(probe, "review_mastodon_tags", keeps_everything)
    correct, invalid, total = await probe._probe_adversarial("key", "model")
    assert 0 < correct < total
    assert "WRONG" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_perfect_reviewer_scores_perfectly(monkeypatch):
    """Sanity check on the scoring itself, using the cases' own expectations."""
    async def oracle(key, post, cands, model):
        # Positive controls are the three tags the case table marks must-keep.
        keep = [c in ("#OOP", "#BrowserExtensions") or
                (c == "#NVIDIA" and "CoWoS" in post) for c in cands]
        return keep, [c for c, k in zip(cands, keep) if k]

    monkeypatch.setattr(probe, "review_mastodon_tags", oracle)
    correct, invalid, total = await probe._probe_adversarial("key", "model")
    assert (correct, invalid) == (total, 0)


@pytest.mark.asyncio
async def test_an_unparseable_verdict_is_never_scored_as_a_rejection(
    monkeypatch, capsys
):
    """The finding that mattered: `[]` from a parser failure is not a semantic
    rejection. A model returning junk must not post a perfect record."""
    async def unparseable(key, post, cands, model):
        return None, []

    monkeypatch.setattr(probe, "review_mastodon_tags", unparseable)
    correct, invalid, total = await probe._probe_adversarial("key", "model")
    assert correct == 0
    assert invalid == total
    assert "INVALID" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_probe_skips_a_post_that_already_spends_the_ceiling(monkeypatch, capsys):
    called = []

    async def one_call(key, post, model, allowance):
        called.append(1)
        return [], []

    monkeypatch.setattr(probe, "request_mastodon_tags", one_call)
    tagged = await probe._probe_one("key", "proposer", "reviewer", 1, "a #Python #Linux post")

    assert called == []
    assert tagged is False
    assert "ceiling" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_failing_call_does_not_stop_the_run(monkeypatch, capsys):
    async def boom(key, post, model, allowance):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(probe, "request_mastodon_tags", boom)
    assert await probe._probe_one("key", "proposer", "reviewer", 1, "a note") is False
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
