"""Mastodon-only discovery tags (v4.26).

Tags are named per-post by a small follow-up model call that reads the finished
text, then appended to the Mastodon copy's root post at broadcast time. The
Bluesky copy never gets them.

The properties worth pinning: the tagger cannot fail a run (every error path
ships the post untagged), the sanitizer refuses broad/malformed/duplicate tags,
and the append never overflows the platform cap or trims content to fit.
"""
import json
import pytest

from src.config import MAX_POST_LENGTH_MASTODON, MASTODON_MAX_TAGS
from src import agents, broadcasters


# ── sanitizer ───────────────────────────────────────────────────────────────

def test_well_formed_tags_pass_through():
    assert agents._sanitize_mastodon_tags(["#Semiconductors", "#NVIDIA"]) == \
        ["#Semiconductors", "#NVIDIA"]


def test_missing_hash_is_added():
    assert agents._sanitize_mastodon_tags(["Robotics"]) == ["#Robotics"]


def test_broad_tags_are_dropped():
    """#AI and friends are too fast-moving to earn a boost, and STYLE_GUIDELINES
    already bans that shape."""
    assert agents._sanitize_mastodon_tags(["#AI", "#Semiconductors"]) == ["#Semiconductors"]
    assert agents._sanitize_mastodon_tags(["#tech", "#Innovation", "#TechNews"]) == []


def test_malformed_entries_are_dropped_not_raised():
    raw = ["#has space", "#", "###", "", "#123numeric", None, 42, {"a": 1}, "#Python"]
    assert agents._sanitize_mastodon_tags(raw) == ["#Python"]


def test_non_list_input_yields_no_tags():
    for raw in ({"tags": ["#Python"]}, "#Python", None, 7):
        assert agents._sanitize_mastodon_tags(raw) == []


def test_duplicates_are_collapsed_case_insensitively():
    assert agents._sanitize_mastodon_tags(["#Python", "#python", "#PYTHON"]) == ["#Python"]


def test_tag_already_in_the_post_is_dropped():
    """Repeating a hashtag the post already carries reads as a bot tell."""
    assert agents._sanitize_mastodon_tags(
        ["#Python", "#Robotics"], "teaching #python to my niece"
    ) == ["#Robotics"]


def test_respects_max_tags():
    out = agents._sanitize_mastodon_tags(["#One", "#Two", "#Three", "#Four"])
    assert len(out) == MASTODON_MAX_TAGS


# ── generator: must never be able to fail a run ─────────────────────────────

@pytest.mark.asyncio
async def test_generator_returns_model_tags(monkeypatch):
    async def fake_thread(fn, *args, **kwargs):
        return json.dumps(["#Semiconductors", "#NVIDIA"])

    monkeypatch.setattr(agents.asyncio, "to_thread", fake_thread)
    tags = await agents.generate_mastodon_tags("key", ["a post about chips"])
    assert tags == ["#Semiconductors", "#NVIDIA"]


@pytest.mark.asyncio
async def test_generator_strips_code_fences(monkeypatch):
    async def fake_thread(fn, *args, **kwargs):
        return '```json\n["#RetroComputing"]\n```'

    monkeypatch.setattr(agents.asyncio, "to_thread", fake_thread)
    assert await agents.generate_mastodon_tags("key", ["p"]) == ["#RetroComputing"]


@pytest.mark.asyncio
async def test_model_error_yields_no_tags_not_an_exception(monkeypatch):
    """The post is already written and validated by this point. A tagging
    failure must never take the run down with it."""
    async def boom(fn, *args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(agents.asyncio, "to_thread", boom)
    assert await agents.generate_mastodon_tags("key", ["p"]) == []


@pytest.mark.asyncio
async def test_unparseable_output_yields_no_tags(monkeypatch):
    async def garbage(fn, *args, **kwargs):
        return "Sure! Here are some hashtags for you:"

    monkeypatch.setattr(agents.asyncio, "to_thread", garbage)
    assert await agents.generate_mastodon_tags("key", ["p"]) == []


@pytest.mark.asyncio
async def test_model_returning_only_broad_tags_yields_none(monkeypatch):
    async def broad(fn, *args, **kwargs):
        return json.dumps(["#AI", "#Tech"])

    monkeypatch.setattr(agents.asyncio, "to_thread", broad)
    assert await agents.generate_mastodon_tags("key", ["p"]) == []


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


# ── append ──────────────────────────────────────────────────────────────────

def test_tags_land_on_root_post_only():
    out = broadcasters.apply_mastodon_tags(["root", "second", "third"], ["#LLM", "#Python"])
    assert out[0] == "root\n\n#LLM #Python"
    assert out[1:] == ["second", "third"]


def test_original_list_is_not_mutated():
    original = ["root", "second"]
    broadcasters.apply_mastodon_tags(original, ["#LLM"])
    assert original == ["root", "second"]


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
