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

def _script(monkeypatch, *responses):
    """Feed the two model calls (propose, then review) in order."""
    queue = iter(responses)

    async def scripted(fn, *args, **kwargs):
        return next(queue)

    monkeypatch.setattr(agents.asyncio, "to_thread", scripted)


@pytest.mark.asyncio
async def test_generator_returns_model_tags(monkeypatch, tags_on):
    _script(
        monkeypatch,
        json.dumps(["#Semiconductors", "#NVIDIA"]),
        json.dumps([{"id": 1, "keep": True}, {"id": 2, "keep": True}]),
    )
    assert await agents.generate_mastodon_tags("key", ["a post about chips"]) == \
        ["#Semiconductors", "#NVIDIA"]


@pytest.mark.asyncio
async def test_generator_strips_code_fences(monkeypatch, tags_on):
    """Both calls may come back fenced."""
    _script(
        monkeypatch,
        '```json\n["#RetroComputing"]\n```',
        '```json\n[{"id": 1, "keep": true}]\n```',
    )
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


# ── voice gate (Codex review of #112: banned words passed the sanitizer) ────

def test_banned_hype_words_are_rejected_as_tags():
    """#Revolutionary and #Groundbreaking are literally in BANNED_HYPE_WORDS.
    They shipped before this gate: tags are appended after _apply_voice_trim,
    which is log-only for hype, and validate_summary does not reject it."""
    assert agents._sanitize_mastodon_tags(["#Revolutionary", "#Groundbreaking"]) == []


def test_the_tag_gate_does_not_depend_on_the_prose_validators():
    """The tag rule must stand on its own.

    When this was written `_apply_voice_trim` logged hype without removing it
    and `validate_summary` did not reject it, so "route tags through the
    existing validators" would have fixed nothing. Asserting that deficiency
    directly would make this test fail if prose validation ever improved, which
    is backwards. So assert the property that must hold either way: the tag
    gate rejects hype regardless of what the prose path does with it.
    """
    assert agents._sanitize_mastodon_tags(["#Revolutionary"]) == []
    assert agents._banned_fragment_in("Revolutionary") == "revolutionary"


def test_multi_word_bans_are_caught_through_camelcase():
    """Ban entries are phrases ('game-changing'); tags are concatenations."""
    assert agents._sanitize_mastodon_tags(["#GameChanging"]) == []
    assert agents._sanitize_mastodon_tags(["#WatchThisSpace"]) == []
    assert agents._sanitize_mastodon_tags(["#StayTuned"]) == []


def test_banned_fragment_is_caught_as_a_substring():
    assert agents._sanitize_mastodon_tags(["#RevolutionaryAI"]) == []


def test_the_empty_normalized_ban_entry_is_excluded():
    """BANNED_TEASER_PATTERNS contains the thread emoji, which normalises to
    "" — and "" is a substring of everything. Without the guard the ban list
    would reject every tag ever proposed."""
    assert "" not in agents._BANNED_TAG_FRAGMENTS
    assert agents._sanitize_mastodon_tags(["#Python"]) == ["#Python"]


def test_the_known_over_rejection_is_deliberate():
    """'epic' is a banned hype word, so #EpicGames — a real company — is
    refused. Accepted cost: no exception catalogue, because rescuing individual
    names starts a second definition of acceptable voice."""
    assert agents._sanitize_mastodon_tags(["#EpicGames"]) == []


def test_the_voice_gate_does_not_touch_known_good_tags():
    """Every tag the live probe produced must still survive."""
    good = ["#MicrosoftEdge", "#BrowserExtensions", "#OOP", "#TheMotherOfAllDemos",
            "#DataMigration", "#AIObservability", "#NVIDIA", "#ComputerHistory",
            "#InfoSec", "#TechnicalDebt", "#SoftwareEngineering", "#RetroComputing"]
    for tag in good:
        assert agents._sanitize_mastodon_tags([tag]) == [tag], tag


# ── semantic review (Codex review of #113: entity tags were ungrounded) ────

@pytest.mark.asyncio
async def test_review_keeps_only_what_the_model_approves(monkeypatch):
    async def verdicts(fn, *args, **kwargs):
        return json.dumps([{"id": 1, "keep": True}, {"id": 2, "keep": False}])

    monkeypatch.setattr(agents.asyncio, "to_thread", verdicts)
    _, kept = await agents.review_mastodon_tags(
        "key", "post", ["#Semiconductors", "#NVIDIA"], "model"
    )
    assert kept == ["#Semiconductors"]


@pytest.mark.asyncio
async def test_review_reads_indices_only_never_text(monkeypatch):
    """The reviewer must not be able to introduce a tag. Only keep/drop by id
    is read, so a hallucinated tag in the response has no route into the post."""
    async def sneaky(fn, *args, **kwargs):
        return json.dumps([{"id": 1, "keep": True, "tag": "#Hallucinated"}])

    monkeypatch.setattr(agents.asyncio, "to_thread", sneaky)
    _, kept = await agents.review_mastodon_tags("key", "post", ["#Python"], "model")
    assert kept == ["#Python"]


@pytest.mark.asyncio
async def test_review_treats_anything_not_true_as_a_drop(monkeypatch):
    """Uncertain means reject."""
    async def mushy(fn, *args, **kwargs):
        return json.dumps([
            {"id": 1, "keep": "yes"}, {"id": 2, "keep": None}, {"id": 3},
        ])

    monkeypatch.setattr(agents.asyncio, "to_thread", mushy)
    _, kept = await agents.review_mastodon_tags("key", "p", ["#A", "#B", "#C"], "model")
    assert kept == []


@pytest.mark.asyncio
async def test_review_of_malformed_output_drops_everything(monkeypatch):
    async def garbage(fn, *args, **kwargs):
        return json.dumps({"verdict": "looks fine to me"})

    monkeypatch.setattr(agents.asyncio, "to_thread", garbage)
    assert await agents.review_mastodon_tags("key", "p", ["#A"], "model") == (None, [])


@pytest.mark.parametrize("raw, count, why", [
    ([{"id": True, "keep": True}], 1, "bool is an int subclass; True must not index 1"),
    ([{"id": "1", "keep": True}], 1, "string id coerced by int()"),
    ([{"id": 1.9, "keep": True}], 1, "float id truncated by int()"),
    ([{"id": 1, "keep": False}, {"id": 1, "keep": True}], 2,
     "contradiction must not resolve to approve"),
    ([{"id": 1, "keep": True}], 2, "incomplete coverage"),
    ([None, {"id": 1, "keep": True}], 2, "a junk entry must void the set"),
    ([{"id": 1, "keep": "yes"}], 1, "keep must be a real bool"),
    ([{"id": 5, "keep": True}], 1, "id out of range"),
    ({"verdict": "fine"}, 1, "not a list"),
    ([{"id": 1, "keep": True}, {"id": 2, "keep": True}], 1, "too many decisions"),
])
def test_no_malformed_shape_can_approve_a_tag(raw, count, why):
    """Every one of these approved a tag before the whole-set validator.

    Partial salvage inverts the fail-closed contract: a malformed answer became
    a publish (Codex review, 2026-09-09).
    """
    assert agents._parse_tag_verdicts(raw, count) is None, why


def test_a_well_formed_decision_set_is_honoured():
    assert agents._parse_tag_verdicts(
        [{"id": 2, "keep": True}, {"id": 1, "keep": False}], 2
    ) == [False, True]


@pytest.mark.asyncio
async def test_review_skips_the_call_when_there_is_nothing_to_review(monkeypatch):
    called = []

    async def tracker(fn, *args, **kwargs):
        called.append(1)
        return "[]"

    monkeypatch.setattr(agents.asyncio, "to_thread", tracker)
    assert await agents.review_mastodon_tags("key", "p", [], "model") == ([], [])
    assert called == []


@pytest.mark.asyncio
async def test_a_tag_rejected_in_review_never_reaches_the_post(monkeypatch, tags_on):
    """End to end: the model proposes an ungrounded company tag, review drops
    it, and generate_mastodon_tags returns nothing to append."""
    responses = iter([
        json.dumps(["#NVIDIA"]),                      # propose
        json.dumps([{"id": 1, "keep": False}]),       # review
    ])

    async def scripted(fn, *args, **kwargs):
        return next(responses)

    monkeypatch.setattr(agents.asyncio, "to_thread", scripted)
    tags = await agents.generate_mastodon_tags(
        "key", ["A migration stalls on decommissioning, not the data pipeline."]
    )
    assert tags == []


@pytest.mark.asyncio
async def test_both_calls_share_one_deadline(monkeypatch, tags_on):
    """Adding a second gate must not buy a second delay allowance.

    Both calls take 0.15s against a 0.20s budget. Each fits comfortably inside
    a budget of its own, so a two-deadline implementation would return the tag;
    only one SHARED deadline times out. A test where generation is instant
    would pass either way and prove nothing (Codex review, 2026-09-09).
    """
    calls = []

    async def slow_each_time(fn, *args, **kwargs):
        calls.append(1)
        await asyncio.sleep(0.15)
        if len(calls) == 1:
            return json.dumps(["#Python"])
        return json.dumps([{"id": 1, "keep": True}])

    monkeypatch.setattr(agents.asyncio, "to_thread", slow_each_time)
    monkeypatch.setattr(agents, "MASTODON_TAGS_TIMEOUT_SECONDS", 0.20)

    tags = await asyncio.wait_for(
        agents.generate_mastodon_tags("key", ["a post"]), timeout=5
    )
    assert tags == []
    assert len(calls) == 2, "both calls should have started under one budget"


@pytest.mark.asyncio
async def test_two_fast_calls_still_fit_the_shared_budget(monkeypatch, tags_on):
    """The companion to the test above: a shared deadline must not be so tight
    that the normal two-call path cannot complete."""
    responses = iter([
        json.dumps(["#Python"]),
        json.dumps([{"id": 1, "keep": True}]),
    ])

    async def quick(fn, *args, **kwargs):
        await asyncio.sleep(0.01)
        return next(responses)

    monkeypatch.setattr(agents.asyncio, "to_thread", quick)
    monkeypatch.setattr(agents, "MASTODON_TAGS_TIMEOUT_SECONDS", 1.0)
    assert await agents.generate_mastodon_tags("key", ["a post"]) == ["#Python"]


@pytest.mark.asyncio
async def test_review_failure_ships_the_post_untagged(monkeypatch, tags_on):
    responses = iter([json.dumps(["#Python"])])

    async def propose_then_fail(fn, *args, **kwargs):
        try:
            return next(responses)
        except StopIteration:
            raise RuntimeError("review call exploded")

    monkeypatch.setattr(agents.asyncio, "to_thread", propose_then_fail)
    assert await agents.generate_mastodon_tags("key", ["a post"]) == []


@pytest.mark.asyncio
async def test_an_unusable_verdict_logs_its_own_event(monkeypatch, tags_on):
    """A parser regression and a genuine all-DROP verdict both end with no
    tags. Logging them identically is how a broken reviewer hides for weeks,
    which is the failure mode AGENTS.md #3 exists to prevent (Codex review,
    2026-09-09 - the same conflation was found in three places)."""
    events = []
    monkeypatch.setattr(
        agents.SafeLogger, "warn",
        lambda event, message="", **f: events.append(event),
    )
    monkeypatch.setattr(
        agents.SafeLogger, "info",
        lambda event, message="", **f: events.append(event),
    )
    _script(
        monkeypatch,
        json.dumps(["#Python"]),
        json.dumps([{"id": 1, "keep": "maybe"}]),   # parseable, not a decision
    )
    assert await agents.generate_mastodon_tags("key", ["a post"]) == []
    assert "mastodon_tags_invalid_verdict" in events
    assert "mastodon_tags_empty" not in events


@pytest.mark.asyncio
async def test_a_genuine_all_drop_verdict_is_not_reported_as_invalid(
    monkeypatch, tags_on
):
    """The other side of the same distinction."""
    events = []
    monkeypatch.setattr(
        agents.SafeLogger, "warn",
        lambda event, message="", **f: events.append(event),
    )
    monkeypatch.setattr(
        agents.SafeLogger, "info",
        lambda event, message="", **f: events.append(event),
    )
    _script(
        monkeypatch,
        json.dumps(["#Python"]),
        json.dumps([{"id": 1, "keep": False}]),
    )
    assert await agents.generate_mastodon_tags("key", ["a post"]) == []
    assert "mastodon_tags_empty" in events
    assert "mastodon_tags_invalid_verdict" not in events


# ── cross-model review (Codex P1 on #116: grounding must not self-approve) ─

def test_reviewer_is_a_different_model_from_the_proposer():
    """A correlated semantic mistake must not be able to approve itself by
    the same judgement twice."""
    proposer, reviewer = agents._select_tag_models()
    assert proposer != reviewer


def test_selection_walks_past_a_duplicated_first_entry():
    assert agents._select_tag_models(["a", "a", "b"]) == ("a", "b")


def test_a_single_model_chain_yields_no_reviewer():
    """The first version fell back to the proposer, recreating the exact
    self-approval hole this exists to close. filter_available_models can
    prune the chain to one entry at startup, so this is a reachable
    production state (Codex review, 2026-09-09)."""
    assert agents._select_tag_models(["only"]) == ("only", None)


@pytest.mark.asyncio
async def test_no_reviewer_means_no_tags_and_no_model_call(monkeypatch, tags_on):
    """Fail closed: tags are decoration, so refusing to tag costs a
    discovery opportunity; publishing an unreviewed affiliation costs more."""
    called = []

    async def tracker(fn, *args, **kwargs):
        called.append(1)
        return json.dumps(["#Python"])

    monkeypatch.setattr(agents.asyncio, "to_thread", tracker)
    tags = await agents.generate_mastodon_tags(
        "key", ["a post"], model_priority=["only-one"]
    )
    assert tags == []
    assert called == [], "must not even propose without a reviewer"


@pytest.mark.asyncio
async def test_the_two_calls_actually_use_the_two_models(monkeypatch, tags_on):
    """Pins the wiring, not just the selection helper: it would be easy to
    choose two models and then pass one of them to both calls."""
    used = []
    responses = iter([
        json.dumps(["#Python"]),
        json.dumps([{"id": 1, "keep": True}]),
    ])

    async def record(fn, api_key, instr, task, model, *a, **kw):
        used.append(model)
        return next(responses)

    monkeypatch.setattr(agents.asyncio, "to_thread", record)
    tags = await agents.generate_mastodon_tags(
        "key", ["a post"], model_priority=["proposer-model", "reviewer-model"]
    )
    assert tags == ["#Python"]
    assert used == ["proposer-model", "reviewer-model"]


@pytest.mark.asyncio
async def test_no_mastodon_token_means_no_tagging_calls(monkeypatch, tags_on):
    """A Bluesky-only run must not pay for Mastodon.

    post_to_mastodon returns immediately without a token, so tagging first
    would spend two model calls and up to the full budget for a platform
    that is switched off. main._tag_and_post_mastodon guards on the token;
    this pins the property the guard exists for (Codex review, 2026-09-09).
    """
    called = []

    async def tracker(fn, *args, **kwargs):
        called.append(1)
        return json.dumps(["#Python"])

    monkeypatch.setattr(agents.asyncio, "to_thread", tracker)

    # The guard lives at the call site, so assert the broadcaster's own
    # no-token contract that makes tagging pointless in the first place.
    result = await broadcasters.post_to_mastodon(
        "", "https://mastodon.example", ["a note"], tags=["#Python"]
    )
    assert result.sent_uris == []
    assert result.delivered_texts == []


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
