"""Behaviour of src/agents.py that the 2026-09-11 mutation run found unpinned.

Each section names the lines whose surviving mutants its tests kill. Line
numbers refer to agents.py at 0eff828. Left alone as equivalent or noise:
  - log-only lines: L264, L276-L277, L878-L896 (the sanitizer's rejection
    log), L1318, L1342-L1343 (model discovery's "removed" log), L1604 (the
    logged reason);
  - the retry sleeps: L424-L425, L1624-L1625;
  - L411 and L1605 (raise -> pass): the invalid draft then falls through to
    the next attempt without its log line, the same outcome;
  - L107 (break -> continue in _fit_reply): sentence ends only grow, so every
    later one is past the limit too;
  - L113: the configured minimum reply delay is above 1 second anyway;
  - L583 and L587 (0 -> 1, 1 -> 2): every count shifts or scales alike, so
    the ranking is unchanged;
  - L957 (Or -> And): json.loads never returns a non-list of the right length
    whose entries are dicts;
  - L239: reachable only when the dash trim leaves a second teaser in place
    ("Stay tuned! — more soon"), which the trim does not handle anyway;
  - L1259 and L1284, the visual-prompt timeout and summary cap.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import agents
from src.config import (
    CURATOR_PERSONA_VARIANTS,
    GEMINI_MODEL_PRIORITY,
    MENTOR_PERSONA_VARIANTS,
    MENTOR_TOPICS,
    SECONDARY_TOPICS,
    Mode,
)

_GOOD_POST = "This is a long enough primary post with enough detail to pass validation."
_BARE = json.dumps([_GOOD_POST])
_REPLY = "Worth adding that the paper measured this on real hardware, not a simulator."


async def _no_sleep(*_args, **_kwargs):
    return None


def _script_generate(monkeypatch, answer):
    """Replace _sync_generate with answer(model, call_number); record the models."""
    calls = []

    def fake(api_key, system_instr, task, model):
        calls.append(model)
        result = answer(model, len(calls))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(agents, "_sync_generate", fake)
    monkeypatch.setattr(agents.asyncio, "sleep", _no_sleep)
    return calls


# ---------------------------------------------------------------------------
# Mention replies (L1691, L1697, L1705, L1707, L1724, L1740, L1742, L1748).
# These reach real people, so every skip must skip only its own mention.
# ---------------------------------------------------------------------------

def _notification(uri, *, reason="mention", is_read=False):
    return SimpleNamespace(uri=uri, cid=f"cid-{uri}", reason=reason, is_read=is_read,
                           record=SimpleNamespace(text="Any advice on architecture tradeoffs?"),
                           author=SimpleNamespace(handle="alice.example"))


async def _handle(monkeypatch, notifications, *, replies=("A perfectly good reply.",), rolls=(), known=()):
    """Run handle_interactions. Returns (replied-to URIs, recorded URIs, models used)."""
    sent, recorded, models = [], [], []
    replies, rolls = list(replies), list(rolls)

    async def list_notifications():
        return SimpleNamespace(notifications=notifications)

    async def send_post(text, reply_to):
        sent.append(reply_to["parent"]["uri"])

    def generate(api_key, system_instr, task, model):
        models.append(model)
        return replies.pop(0) if len(replies) > 1 else replies[0]

    def update(mutator):
        recorded[:] = mutator(list(recorded))
        return recorded

    client = SimpleNamespace(
        app=SimpleNamespace(bsky=SimpleNamespace(notification=SimpleNamespace(list_notifications=list_notifications))),
        send_post=send_post,
    )
    monkeypatch.setattr(agents, "load_replied_to_strict", lambda: (list(known), True))
    monkeypatch.setattr(agents, "update_replied_to", update)
    monkeypatch.setattr(agents, "_sync_generate", generate)
    monkeypatch.setattr(agents.random, "random", lambda: rolls.pop(0) if rolls else 0.95)  # 0.95: never skip
    monkeypatch.setattr(agents.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(agents.asyncio, "sleep", _no_sleep)
    await agents.handle_interactions(client, "key")
    return sent, recorded, models


@pytest.mark.asyncio
async def test_only_unread_mentions_get_a_reply(monkeypatch):
    notifications = [
        _notification("at://like", reason="like"),
        _notification("at://reply", reason="reply"),
        _notification("at://read-mention", is_read=True),
        _notification("at://new-mention"),
    ]
    sent, _recorded, models = await _handle(monkeypatch, notifications)
    assert sent == ["at://new-mention"]
    assert models == [GEMINI_MODEL_PRIORITY[0]]


@pytest.mark.asyncio
async def test_an_answered_mention_skips_only_itself(monkeypatch):
    sent, _recorded, _models = await _handle(
        monkeypatch, [_notification("at://m1"), _notification("at://m2")], known=["at://m1"])
    assert sent == ["at://m2"]


@pytest.mark.asyncio
async def test_a_cadence_skip_skips_only_that_mention(monkeypatch):
    sent, recorded, _models = await _handle(
        monkeypatch, [_notification("at://m1"), _notification("at://m2")], rolls=[0.0, 0.95])
    assert sent == ["at://m2"]
    assert recorded == ["at://m1", "at://m2"]


@pytest.mark.asyncio
async def test_an_unfit_reply_skips_only_that_mention(monkeypatch):
    sent, recorded, _models = await _handle(
        monkeypatch, [_notification("at://m1"), _notification("at://m2")],
        replies=["", "A perfectly good reply."])
    assert sent == ["at://m2"]
    assert recorded == ["at://m1", "at://m2"]


@pytest.mark.parametrize("replies, rolls, replies_sent", [
    (["A perfectly good reply."], [], 1),               # answered the first time (L1748)
    (["", "A perfectly good reply."], [], 0),           # unfit the first time (L1740)
    (["A perfectly good reply."], [0.0, 0.95], 0),      # cadence-skipped the first time (L1705)
])
@pytest.mark.asyncio
async def test_a_mention_listed_twice_is_handled_once(monkeypatch, replies, rolls, replies_sent):
    """The in-run set is what stops a duplicate listing getting a second pass."""
    sent, _recorded, _models = await _handle(
        monkeypatch, [_notification("at://m1"), _notification("at://m1")], replies=replies, rolls=rolls)
    assert len(sent) == replies_sent


# ---------------------------------------------------------------------------
# Mode routing and the output format (L569, L1402, L1425, L1441, L1474, L1475)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode, variants", [(Mode.CURATOR, CURATOR_PERSONA_VARIANTS),
                                            (Mode.MENTOR, MENTOR_PERSONA_VARIANTS)])
def test_each_mode_draws_from_its_own_persona_variants(mode, variants):
    name, instruction = agents._select_persona_variant(mode)
    assert variants[name] == instruction


@pytest.mark.asyncio
async def test_a_strategist_run_stays_a_strategist_run(monkeypatch):
    _script_generate(monkeypatch, lambda model, n: _BARE)
    content, topic, _link = await agents.generate_content("key", [], mode=Mode.STRATEGIST)
    assert content == [_GOOD_POST]
    assert topic in SECONDARY_TOPICS


@pytest.mark.asyncio
async def test_a_mentor_run_given_news_items_still_writes_a_mentor_post(monkeypatch):
    """News items are the Curator's input. A Mentor run that receives them keeps
    its own topic pool and its bare-array output format."""
    _script_generate(monkeypatch, lambda model, n: _BARE)
    news = [{"title": "Some headline", "description": "d", "link": "https://example.com/a"}]
    content, topic, link = await agents.generate_content("key", [], mode=Mode.MENTOR, news_items=news)
    assert content == [_GOOD_POST]
    assert topic in MENTOR_TOPICS
    assert link is None


@pytest.mark.parametrize("mode, news, expected", [
    (Mode.CURATOR, [{"title": "Headline", "description": "d", "link": "https://example.com/a"}],
     "Return ONLY a JSON object"),
    (Mode.MENTOR, None, "Return ONLY a JSON array of strings"),
])
@pytest.mark.asyncio
async def test_each_mode_is_told_its_own_output_format(monkeypatch, mode, news, expected):
    tasks = []

    def fake(api_key, system_instr, task, model):
        tasks.append(task)
        raise RuntimeError("stop here")

    monkeypatch.setattr(agents, "_sync_generate", fake)
    await agents.generate_content("key", [], mode=mode, news_items=news)
    assert expected in tasks[0]


# ---------------------------------------------------------------------------
# Retry and fallback (L376, L437, L1550, L1643)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_overlong_post_is_regenerated_not_shipped(monkeypatch):
    overlong = json.dumps(["A sentence about context windows and the tools we use. " * 6])
    calls = _script_generate(monkeypatch, lambda model, n: overlong if n == 1 else _BARE)
    content, _topic, _link = await agents.generate_content("key", [], mode=Mode.MENTOR)
    assert content == [_GOOD_POST]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_an_api_error_moves_straight_to_the_next_model(monkeypatch):
    calls = _script_generate(monkeypatch, lambda model, n: RuntimeError("quota") if model == "model-a" else _BARE)
    content, _topic, _link = await agents.generate_content(
        "key", [], mode=Mode.MENTOR, model_priority=["model-a", "model-b"])
    assert content == [_GOOD_POST]
    assert calls == ["model-a", "model-b"]


@pytest.mark.asyncio
async def test_a_proactive_draft_gets_two_attempts_per_model(monkeypatch):
    calls = _script_generate(monkeypatch, lambda model, n: "Too short.")
    assert await agents.generate_proactive_reply("key", "alice", "post", model_priority=["model-a", "model-b"]) is None
    assert calls == ["model-a", "model-a", "model-b", "model-b"]


@pytest.mark.asyncio
async def test_a_proactive_api_error_moves_straight_to_the_next_model(monkeypatch):
    calls = _script_generate(monkeypatch, lambda model, n: RuntimeError("quota") if model == "model-a" else _REPLY)
    assert await agents.generate_proactive_reply("key", "alice", "post", model_priority=["model-a", "model-b"]) == _REPLY
    assert calls == ["model-a", "model-b"]


def test_a_proactive_reply_of_exactly_30_chars_is_long_enough():
    text = "Context windows changed things"
    assert len(text) == 30
    assert agents._validate_proactive_reply(text) == (True, "OK")
    assert agents._validate_proactive_reply(text[:-1])[0] is False


# ---------------------------------------------------------------------------
# Validation return values (L144, L551-L555)
# ---------------------------------------------------------------------------

def test_an_empty_post_fails_validation():
    assert agents.validate_summary("") == (False, "Empty output")


@pytest.mark.parametrize("content", [
    {"posts": [_GOOD_POST]},   # not a list
    [_GOOD_POST, "   "],       # an empty entry
    [_GOOD_POST, 5],           # a non-string entry
    [],                        # too few posts
    [_GOOD_POST] * 6,          # too many posts
])
def test_a_malformed_thread_is_rejected(content):
    ok, _reason = agents._validate_thread_shape(content)
    assert ok is False


# ---------------------------------------------------------------------------
# Voice trims (L210, L230)
# ---------------------------------------------------------------------------

def test_a_teaser_before_a_dash_still_counts():
    assert agents._ends_with_teaser("We covered the release notes. More soon — details on the new models.") is True


def test_a_teaser_trim_never_leaves_a_post_below_the_minimum():
    text = "Short take. More soon."
    assert agents._strip_trailing_teaser(text) == text


# ---------------------------------------------------------------------------
# Prompt building: day theme, style memory, thinking budget
# (L125-L134, L582-L601, L612-L635, L729)
# ---------------------------------------------------------------------------

def _freeze(monkeypatch, when):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    monkeypatch.setattr(agents, "datetime", _Frozen)


@pytest.mark.parametrize("when, day, theme", [
    (datetime(2026, 9, 14, 9, tzinfo=timezone.utc), "Monday", "weekly strategy"),
    (datetime(2026, 9, 11, 9, tzinfo=timezone.utc), "Friday", "Capping off the week"),
    (datetime(2026, 9, 12, 9, tzinfo=timezone.utc), "Saturday", "Weekend"),
    (datetime(2026, 9, 13, 9, tzinfo=timezone.utc), "Sunday", "Weekend"),
    (datetime(2026, 9, 16, 9, tzinfo=timezone.utc), "Wednesday", "Mid-week"),
])
def test_each_day_gets_its_theme(monkeypatch, when, day, theme):
    _freeze(monkeypatch, when)
    context = agents.get_temporal_context()
    assert context["day"] == day
    assert theme in context["theme"]


@pytest.mark.parametrize("hour, session", [(11, "Morning"), (12, "Afternoon")])
def test_the_session_turns_at_noon_utc(monkeypatch, hour, session):
    _freeze(monkeypatch, datetime(2026, 9, 16, hour, tzinfo=timezone.utc))
    assert agents.get_temporal_context()["session"].startswith(session)


def test_style_memory_ranks_openers_and_hashtags_by_use():
    fingerprints = agents._extract_style_fingerprints([
        "One two three four five #B #A",
        "One two three four seven #B #A",
        "Other opener here now #B #C",
    ])
    assert fingerprints["repeated_openers"] == ["one two three four", "other opener here now"]
    assert fingerprints["repeated_hashtags"] == ["#b", "#a", "#c"]  # used 3, 2 and 1 times


def test_a_long_recent_post_is_excerpted_to_200_chars():
    constraints = agents._build_avoidance_constraints({}, ["word " * 60])
    excerpt = next(line for line in constraints.splitlines() if line.startswith("  1. "))[len("  1. "):]
    assert len(excerpt) == 200
    assert excerpt.endswith("…")


def test_only_three_recent_posts_are_quoted():
    constraints = agents._build_avoidance_constraints({}, [f"Recent post number {n}" for n in range(1, 6)])
    assert "  3. Recent post number 3" in constraints
    assert "  4. " not in constraints


@pytest.mark.parametrize("model, budget", [
    ("gemini-3.7-flash", 0),
    ("gemini-2.5-pro", 128),
    ("gemini-1.5-flash", None),   # no thinking mode: sending the config errors
])
def test_the_thinking_budget_is_sent_only_where_it_applies(model, budget):
    config = agents._build_generate_kwargs(model, "system", "task")["config"]
    if budget is None:
        assert "thinking_config" not in config
    else:
        assert config["thinking_config"] == {"thinking_budget": budget}


# ---------------------------------------------------------------------------
# Pioneer (L482, L526)
# ---------------------------------------------------------------------------

def test_a_dated_event_fires_on_its_day_and_not_just_its_month(monkeypatch):
    event = {"id": "demo", "month": 12, "day": 9, "title": "The Mother of All Demos", "year": 1968, "detail": "d"}
    monkeypatch.setattr(agents, "PIONEER_EVENTS_DATED", [event])
    monkeypatch.setattr(agents, "PIONEER_FALLBACK_PROBABILITY", 0.0)  # the undated pool never fires
    assert agents.select_pioneer_topic({}, now=datetime(2026, 12, 9, tzinfo=timezone.utc)) == {
        "entry": event, "pool": "dated"}
    assert agents.select_pioneer_topic({}, now=datetime(2026, 12, 10, tzinfo=timezone.utc)) is None


def test_a_dated_pioneer_prompt_carries_the_title_and_year():
    entry = {"title": "The Mother of All Demos", "year": 1968, "detail": "Engelbart's demo.", "link": None}
    task = agents._build_pioneer_task({"entry": entry, "pool": "dated"})
    assert "The Mother of All Demos" in task
    assert "1968" in task


def test_an_empty_topic_pool_raises_a_clear_error():
    with pytest.raises(ValueError, match="empty"):
        agents._pick_topic_avoiding_recent([], [])


# ---------------------------------------------------------------------------
# Mastodon tags (L885, L1026, L1089, L1122)
# ---------------------------------------------------------------------------

def test_a_banned_tag_does_not_stop_the_tags_after_it():
    assert agents._sanitize_mastodon_tags(["#GameChanging", "#Semiconductors"], "", 2) == ["#Semiconductors"]


@pytest.mark.asyncio
async def test_the_reviewer_sees_candidates_numbered_from_one(monkeypatch):
    """The verdicts are read by id 1..n. Numbered any other way in the prompt,
    the reviewer's ids would never line up and every tag would be dropped."""
    tasks = []

    async def fake_thread(fn, api_key, system_instr, task, model):
        tasks.append(task)
        return json.dumps([{"id": 1, "keep": True}, {"id": 2, "keep": False}])

    monkeypatch.setattr(agents.asyncio, "to_thread", fake_thread)
    decisions, kept = await agents.review_mastodon_tags(
        "key", "A post about chip packaging.", ["#Semiconductors", "#NVIDIA"], "reviewer")
    assert "1. #Semiconductors\n2. #NVIDIA" in tasks[0]
    assert (decisions, kept) == ([True, False], ["#Semiconductors"])


@pytest.mark.asyncio
async def test_no_candidates_skips_the_review(monkeypatch):
    async def no_candidates(api_key, post, model, allowance):
        return ["#AI"], []

    monkeypatch.setattr(agents, "request_mastodon_tags", no_candidates)
    assert await agents._tag_pipeline("key", "post", "proposer", "reviewer", 2) == (["#AI"], [], [], [])


@pytest.mark.asyncio
async def test_a_post_with_one_hashtag_can_still_get_one_more(monkeypatch):
    monkeypatch.setattr(agents, "MASTODON_TAGS_ENABLED", True)
    answers = iter([json.dumps(["#Semiconductors"]), json.dumps([{"id": 1, "keep": True}])])

    async def scripted(fn, *args, **kwargs):
        return next(answers)

    monkeypatch.setattr(agents.asyncio, "to_thread", scripted)
    tags = await agents.generate_mastodon_tags(
        "key", ["A post about chip packaging. #Chips"], model_priority=["m1", "m2"])
    assert tags == ["#Semiconductors"]


# ---------------------------------------------------------------------------
# The visual-prompt text call (L1231, L1236)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [("A prompt.", "A prompt."), (None, "")])
def test_the_visual_prompt_call_uses_the_primary_model(monkeypatch, text, expected):
    used = []

    class _Models:
        def generate_content(self, model, contents, config):
            used.append(model)
            return SimpleNamespace(text=text)

    monkeypatch.setattr(agents, "_get_client", lambda key: SimpleNamespace(models=_Models()))
    assert agents._sync_generate_text("key", "system", "task") == expected
    assert used == [GEMINI_MODEL_PRIORITY[0]]
