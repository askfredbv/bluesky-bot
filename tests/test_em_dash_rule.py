"""The voice rule against em-dashes (2026-09-12).

3 of the 50 posts up to 2026-09-11 carried an em-dash, and nothing told the
model not to: the prompts themselves were written with more than 60 of them.
Now the rule sits in STYLE_GUIDELINES, which both system instructions include;
the prompt prose no longer uses the dash; and _apply_voice_trim logs any that
still slip through, without changing the post, so the rule can be measured.

What keeps its dash on purpose is quoted text that is a record, not an
instruction: two of Frederik's published samples, and two BAD example posts
shown as the model wrote them.
"""
import pytest

from src import agents, config
from src.config import Mode

KEPT_QUOTES = (
    '"Conversational AI is changing how we handle',             # Frederik's sample, register A
    '"But I have no iPhone',                                   # Frederik's sample, register B
    '"Insurance policies are starting to get very specific',   # BAD 1, a real failure as written
    '"Voice agents often fail in subtle ways',                  # BAD 3
)


def _without_kept_quotes(text):
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith(KEPT_QUOTES))


PROMPTS = {
    "SYSTEM_INSTRUCTIONS_MENTOR": config.SYSTEM_INSTRUCTIONS_MENTOR,
    "SYSTEM_INSTRUCTIONS_CURATOR": config.SYSTEM_INSTRUCTIONS_CURATOR,
    "PIONEER_PROMPT_DATED": config.PIONEER_PROMPT_DATED,
    "PIONEER_PROMPT_UNDATED": config.PIONEER_PROMPT_UNDATED,
    "PROACTIVE_REPLY_SYSTEM_INSTRUCTIONS": config.PROACTIVE_REPLY_SYSTEM_INSTRUCTIONS,
    "PROACTIVE_REPLY_FEW_SHOT_EXAMPLES": config.PROACTIVE_REPLY_FEW_SHOT_EXAMPLES,
    **{f"MENTOR_PERSONA_VARIANTS[{k}]": v for k, v in config.MENTOR_PERSONA_VARIANTS.items()},
    **{f"CURATOR_PERSONA_VARIANTS[{k}]": v for k, v in config.CURATOR_PERSONA_VARIANTS.items()},
}


# ---------------------------------------------------------------------------
# The rule, and prompts that follow it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["SYSTEM_INSTRUCTIONS_MENTOR", "SYSTEM_INSTRUCTIONS_CURATOR"])
def test_both_system_instructions_carry_the_rule(name):
    assert "NEVER USE AN EM-DASH." in PROMPTS[name]


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_the_prompt_prose_carries_no_em_dash(name):
    assert "—" not in _without_kept_quotes(PROMPTS[name])


def test_only_the_kept_quotes_still_carry_a_dash():
    """Editing or dropping one of the kept quotes means updating KEPT_QUOTES."""
    dashed = [line.strip() for line in config.SYSTEM_INSTRUCTIONS_CURATOR.splitlines() if "—" in line]
    assert len(dashed) == len(KEPT_QUOTES)
    assert all(line.startswith(KEPT_QUOTES) for line in dashed)


_ITEMS = [{"title": "Headline", "description": "d", "link": "https://example.com/a"}]
_PIONEER = {"entry": {"id": "demo", "title": "The Mother of All Demos", "detail": "Engelbart's demo.",
                      "link": None}, "pool": "undated"}


@pytest.mark.parametrize("mode, news, pioneer", [
    (Mode.CURATOR, _ITEMS, None),
    (Mode.MENTOR, None, None),
    (Mode.STRATEGIST, None, None),
    (Mode.MENTOR, None, _PIONEER),
])
@pytest.mark.asyncio
async def test_the_prompt_generate_content_sends_carries_no_em_dash(monkeypatch, mode, news, pioneer):
    """Covers the task and format strings built in agents.py, not just config."""
    sent = []

    def fake(api_key, system_instr, task, model):
        sent.append(f"{system_instr}\n{task}")
        raise RuntimeError("stop here")

    monkeypatch.setattr(agents, "_sync_generate", fake)
    await agents.generate_content("key", [], mode=mode, news_items=news, pioneer_entry=pioneer)
    assert "—" not in _without_kept_quotes(sent[0])


def test_no_topic_in_the_pools_carries_an_em_dash():
    """A Mentor or Strategist topic reaches the prompt as "TOPIC: ...", so a
    dashed topic is as good as dashed prose. Checked entry by entry, because
    generate_content draws one at random: two dashed Mentor topics made the test
    above fail only when one of them was drawn (PR #160's CI, 2026-09-12)."""
    dashed = [topic for topic in (*config.MENTOR_TOPICS, *config.SECONDARY_TOPICS) if "—" in topic]
    assert dashed == []


# ---------------------------------------------------------------------------
# The detector: log it, leave the post alone
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("post, count", [
    ("The rule is simple — and the model keeps forgetting it.", 1),
    ("Two of them — here — in one post.", 2),
    ("A spaced en-dash – used as a dash counts too.", 1),
    ("A range like 2019–2021 is not a dash.", 0),
    ("A clean post with a colon: nothing to log.", 0),
])
def test_an_em_dash_is_logged_and_left_in_place(monkeypatch, post, count):
    events = []
    monkeypatch.setattr(agents.SafeLogger, "warn",
                        lambda event, message="", **fields: events.append((event, fields)))
    assert agents._apply_voice_trim([post]) == [post]
    logged = [fields for event, fields in events if event == "em_dash_detected"]
    assert logged == ([{"post_index": 0, "count": count}] if count else [])
