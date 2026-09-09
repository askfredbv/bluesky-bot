"""Mastodon-only discovery tags (v4.26).

Tags are named per-post by a small model call that reads the finished text,
then appended to the Mastodon copy's root post at broadcast time. The Bluesky
copy never gets them.

Properties worth pinning, several of them from the 2026-09-09 Codex review:
the tagger cannot fail OR delay a run, the shared MAX_HASHTAGS_PER_POST
ceiling is a per-post total rather than a per-source one, and the sanitizer
refuses excluded/malformed/duplicate tags.
"""
import asyncio
import json
import pytest

from src.config import (
    MAX_POST_LENGTH_MASTODON, MAX_HASHTAGS_PER_POST,
    MASTODON_TAGS_TIMEOUT_SECONDS,
)
from src import agents, broadcasters


@pytest.fixture
def tags_on(monkeypatch):
    """The feature ships dormant (MASTODON_TAGS_ENABLED=False) so it can merge
    before the probe has vetted real tag output. Tests of the ON path say so
    rather than inheriting whatever the shipped default happens to be."""
    monkeypatch.setattr(agents, "MASTODON_TAGS_ENABLED", True)


# ── sanitizer ───────────────────────────────────────────────────────────────

def test_well_formed_tags_pass_through():
    assert agents._sanitize_mastodon_tags(["#Semiconductors", "#NVIDIA"]) == \
        ["#Semiconductors", "#NVIDIA"]


def test_missing_hash_is_added():
    assert agents._sanitize_mastodon_tags(["Robotics"]) == ["#Robotics"]


def test_single_letter_tags_are_allowed():
    """#R and #C are real tags; an over-strict shape regex would drop them."""
    assert agents._sanitize_mastodon_tags(["#R"]) == ["#R"]


def test_excluded_mood_tags_are_dropped():
    assert agents._sanitize_mastodon_tags(["#AI", "#Semiconductors"]) == ["#Semiconductors"]
    assert agents._sanitize_mastodon_tags(["#tech", "#Innovation", "#TechNews"]) == []


def test_broad_but_substantive_tags_are_kept():
    """Only mood tags are excluded. An internet-history post really can be
    about the internet — 'prefer the narrower tag' is a prompt preference, not
    a ban (Codex review, 2026-09-09)."""
    assert agents._sanitize_mastodon_tags(["#Internet", "#Computing"]) == \
        ["#Internet", "#Computing"]


def test_malformed_entries_are_dropped_not_raised():
    raw = ["#has space", "#", "###", "", "#123numeric", None, 42, {"a": 1}, "#Python"]
    assert agents._sanitize_mastodon_tags(raw) == ["#Python"]


def test_underscore_and_non_ascii_are_dropped():
    """A documented coverage limit, not a bug: Mastodon permits both, we drop
    them rather than reason about unicode normalisation for a decoration."""
    assert agents._sanitize_mastodon_tags(["#machine_learning", "#café"]) == []


def test_non_list_input_yields_no_tags():
    for raw in ({"tags": ["#Python"]}, "#Python", None, 7):
        assert agents._sanitize_mastodon_tags(raw) == []


def test_duplicates_are_collapsed_case_insensitively():
    assert agents._sanitize_mastodon_tags(["#Python", "#python", "#PYTHON"]) == ["#Python"]


def test_tag_already_in_the_post_is_dropped():
    assert agents._sanitize_mastodon_tags(
        ["#Python", "#Robotics"], "teaching #python to my niece"
    ) == ["#Robotics"]


def test_allowance_caps_the_result():
    assert agents._sanitize_mastodon_tags(["#One", "#Two"], "", 1) == ["#One"]
    assert agents._sanitize_mastodon_tags(["#One", "#Two"], "", 0) == []


# ── generator: cannot fail a run, cannot stall one ─────────────────────────

@pytest.mark.asyncio
async def test_generator_returns_model_tags(monkeypatch, tags_on):
    async def fake_thread(fn, *args, **kwargs):
        return json.dumps(["#Semiconductors", "#NVIDIA"])

    monkeypatch.setattr(agents.asyncio, "to_thread", fake_thread)
    assert await agents.generate_mastodon_tags("key", ["a post about chips"]) == \
        ["#Semiconductors", "#NVIDIA"]


@pytest.mark.asyncio
async def test_generator_strips_code_fences(monkeypatch, tags_on):
    async def fake_thread(fn, *args, **kwargs):
        return '```json\n["#RetroComputing"]\n```'

    monkeypatch.setattr(agents.asyncio, "to_thread", fake_thread)
    assert await agents.generate_mastodon_tags("key", ["p"]) == ["#RetroComputing"]


@pytest.mark.asyncio
async def test_model_error_yields_no_tags_not_an_exception(monkeypatch, tags_on):
    async def boom(fn, *args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(agents.asyncio, "to_thread", boom)
    assert await agents.generate_mastodon_tags("key", ["p"]) == []


@pytest.mark.asyncio
async def test_unparseable_output_yields_no_tags(monkeypatch, tags_on):
    async def garbage(fn, *args, **kwargs):
        return "Sure! Here are some hashtags for you:"

    monkeypatch.setattr(agents.asyncio, "to_thread", garbage)
    assert await agents.generate_mastodon_tags("key", ["p"]) == []


@pytest.mark.asyncio
async def test_a_hung_call_gives_up_at_the_budget(monkeypatch, tags_on):
    """The finding that mattered most in review: an exception returns [], but a
    call that never returns is a different failure. The post must proceed."""
    async def never_returns(fn, *args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(agents.asyncio, "to_thread", never_returns)
    monkeypatch.setattr(agents, "MASTODON_TAGS_TIMEOUT_SECONDS", 0.05)

    tags = await asyncio.wait_for(
        agents.generate_mastodon_tags("key", ["p"]), timeout=5
    )
    assert tags == []


def test_the_budget_is_bounded():
    """A budget long enough to matter is not a budget."""
    assert 0 < MASTODON_TAGS_TIMEOUT_SECONDS <= 30


@pytest.mark.asyncio
async def test_only_one_model_is_tried(monkeypatch, tags_on):
    """One attempt: a retry chain multiplies the budget for a decoration."""
    calls = []

    async def failing(fn, api_key, instr, task, model, *a, **kw):
        calls.append(model)
        raise RuntimeError("nope")

    monkeypatch.setattr(agents.asyncio, "to_thread", failing)
    await agents.generate_mastodon_tags("key", ["p"], model_priority=["m1", "m2", "m3"])
    assert calls == ["m1"]


@pytest.mark.asyncio
async def test_a_post_that_spends_the_ceiling_skips_the_call(monkeypatch, tags_on):
    """No allowance means no reason to spend a model call."""
    called = []

    async def tracker(fn, *args, **kwargs):
        called.append(1)
        return "[]"

    monkeypatch.setattr(agents.asyncio, "to_thread", tracker)
    assert await agents.generate_mastodon_tags("key", ["a #Python #Linux post"]) == []
    assert called == []


@pytest.mark.asyncio
async def test_kill_switch_skips_the_call_entirely(monkeypatch):
    called = []

    async def tracker(fn, *args, **kwargs):
        called.append(1)
        return "[]"

    monkeypatch.setattr(agents.asyncio, "to_thread", tracker)
    monkeypatch.setattr(agents, "MASTODON_TAGS_ENABLED", False)
    assert await agents.generate_mastodon_tags("key", ["p"]) == []
    assert called == []


@pytest.mark.asyncio
async def test_no_posts_yields_no_tags():
    assert await agents.generate_mastodon_tags("key", []) == []


@pytest.mark.asyncio
async def test_request_mastodon_tags_has_no_enablement_gate(monkeypatch):
    """The probe calls this directly so it stays truthful while the feature is
    dormant in production."""
    async def fake_thread(fn, *args, **kwargs):
        return json.dumps(["#Python"])

    monkeypatch.setattr(agents.asyncio, "to_thread", fake_thread)
    monkeypatch.setattr(agents, "MASTODON_TAGS_ENABLED", False)
    raw, tags = await agents.request_mastodon_tags("key", "p", "model")
    assert raw == ["#Python"]
    assert tags == ["#Python"]


# ── append: the shared ceiling ─────────────────────────────────────────────

def test_tags_land_on_root_post_only():
    out = broadcasters.apply_mastodon_tags(["root", "second", "third"], ["#LLM", "#Python"])
    assert out[0] == "root\n\n#LLM #Python"
    assert out[1:] == ["second", "third"]


def test_original_list_is_not_mutated():
    original = ["root", "second"]
    broadcasters.apply_mastodon_tags(original, ["#LLM"])
    assert original == ["root", "second"]


def test_ceiling_is_a_per_post_total_not_a_per_source_one():
    """A root already carrying one hashtag gets one more, not two — otherwise
    the two limits stack and the post ships four, breaking STYLE_GUIDELINES on
    one platform only (Codex review, 2026-09-09)."""
    out = broadcasters.apply_mastodon_tags(["about #Rust today"], ["#Python", "#LLM"])
    assert out[0] == "about #Rust today\n\n#Python"


def test_a_post_at_the_ceiling_gets_no_tags():
    root = "a #Python and #Linux post"
    assert broadcasters.apply_mastodon_tags([root], ["#Rust", "#Go"]) == [root]


def test_appended_tags_never_exceed_the_ceiling():
    out = broadcasters.apply_mastodon_tags(["clean"], ["#A", "#B", "#C", "#D"])
    assert out[0].count("#") == MAX_HASHTAGS_PER_POST


def test_tag_already_present_in_root_is_not_duplicated():
    out = broadcasters.apply_mastodon_tags(["teaching #Python again"], ["#Python", "#LLM"])
    assert out[0] == "teaching #Python again\n\n#LLM"


def test_tags_are_shed_rather_than_overflow_the_cap():
    """Content is never trimmed to make room — tags drop one at a time."""
    root = "x" * (MAX_POST_LENGTH_MASTODON - len("\n\n#AI"))
    out = broadcasters.apply_mastodon_tags([root], ["#AI", "#LLM"])
    assert out[0] == root + "\n\n#AI"
    assert len(out[0]) <= MAX_POST_LENGTH_MASTODON


def test_no_room_for_any_tag_leaves_content_untouched():
    root = "x" * MAX_POST_LENGTH_MASTODON
    assert broadcasters.apply_mastodon_tags([root], ["#AI"]) == [root]


def test_empty_inputs_are_no_ops():
    assert broadcasters.apply_mastodon_tags([], ["#AI"]) == []
    assert broadcasters.apply_mastodon_tags(["hi"], []) == ["hi"]


# ── broadcaster integration ─────────────────────────────────────────────────

class _DummyMastodon:
    def __init__(self, access_token, api_base_url):
        self.posted = []

    def status_post(self, status, in_reply_to_id, visibility, media_ids=None):
        self.posted.append(status)
        return {"id": len(self.posted)}


def _patch_mastodon(monkeypatch):
    instances = []

    def factory(access_token, api_base_url):
        m = _DummyMastodon(access_token, api_base_url)
        instances.append(m)
        return m

    monkeypatch.setattr(broadcasters, "Mastodon", factory)
    return instances


@pytest.mark.asyncio
async def test_post_to_mastodon_sends_tagged_root_and_reports_it(monkeypatch):
    """delivered_texts is what the metrics row records; if it carried the
    pre-append copy the row would describe a post that was never sent."""
    instances = _patch_mastodon(monkeypatch)
    result = await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example", ["a note", "second"],
        tags=["#LLM", "#Python"],
    )

    assert instances[0].posted == ["a note\n\n#LLM #Python", "second"]
    assert result.delivered_texts == ["a note\n\n#LLM #Python", "second"]


@pytest.mark.asyncio
async def test_post_to_mastodon_without_tags_is_unchanged(monkeypatch):
    instances = _patch_mastodon(monkeypatch)
    result = await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example", ["a note"], tags=None
    )

    assert instances[0].posted == ["a note"]
    assert result.delivered_texts == ["a note"]


@pytest.mark.asyncio
async def test_tagged_post_that_would_breach_cap_still_ships_untagged(monkeypatch):
    """The invariant runs on the tagged text; shedding keeps it from tripping."""
    instances = _patch_mastodon(monkeypatch)
    root = "x" * MAX_POST_LENGTH_MASTODON
    result = await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example", [root], tags=["#AI"]
    )

    assert instances[0].posted == [root]
    assert len(result.sent_uris) == 1


@pytest.mark.asyncio
async def test_bluesky_copy_never_receives_tags():
    """The divergence lives in the Mastodon adapter and nowhere else."""
    sent = []

    class DummyPost:
        def __init__(self, idx):
            self.cid = f"cid-{idx}"
            self.uri = f"at://post/{idx}"

    class DummyAsyncClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            sent.append(text)
            return DummyPost(len(sent))

    result = await broadcasters.post_to_bluesky(DummyAsyncClient(), ["a note"])

    assert sent == ["a note"]
    assert result.delivered_texts == ["a note"]
