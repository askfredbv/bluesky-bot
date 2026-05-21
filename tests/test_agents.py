import pytest

from src.agents import generate_content, handle_interactions, _truncate_for_platform, _sync_generate, generate_post_image, _build_generate_kwargs, _validate_thread_shape
from src import agents
from src.config import REPLY_MAX_CHARS


@pytest.mark.asyncio
async def test_generate_content_returns_empty_when_all_models_fail(monkeypatch):
    """v4.18: when every model in the chain fails, return ([], topic).

    Previously the function returned a hardcoded "Notes on {topic} —
    more soon." placeholder that bypassed _apply_voice_trim entirely
    and shipped to production as a credibility-corrosive zero-content
    post. broadcasting_stage now treats the empty list as a signal to
    skip the broadcast — missing one run beats shipping garbage.
    """

    def _always_fail(*args, **kwargs):
        raise RuntimeError("forced generation error")

    monkeypatch.setattr("src.agents._sync_generate", _always_fail)

    content, topic = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="curator",
        news_items=[{"title": "Fallback Topic", "description": "desc", "link": "https://example.com"}],
    )

    assert content == []
    assert topic == "Fallback Topic"


@pytest.mark.asyncio
async def test_generate_content_includes_style_memory_constraints(monkeypatch):
    captured = {"prompt": ""}

    def _capture_prompt(_api_key, system_instr, task, _model):
        captured["prompt"] = f"{system_instr}\n\n{task}"
        return '["This is a long enough primary post with #AI and enough detail to pass validation."]'

    monkeypatch.setattr("src.agents._sync_generate", _capture_prompt)

    await generate_content(
        api_key="fake-key",
        recent_posts=[
            "Automation helps teams ship faster #AI #DevOps",
            "Automation helps teams ship safer #AI #Reliability",
            "Documentation matters for onboarding #DevOps #Docs",
            "Documentation matters for shared context #Docs",
        ],
        mode="mentor",
    )

    assert "RECENT STYLE SIGNALS (AVOID REPETITION)" in captured["prompt"]
    assert "Reused opening patterns" in captured["prompt"]
    assert "Reused hashtags" in captured["prompt"]


@pytest.mark.asyncio
async def test_generate_content_includes_persona_variant(monkeypatch):
    captured = {"prompt": ""}

    def _capture_prompt(_api_key, system_instr, task, _model):
        captured["prompt"] = f"{system_instr}\n\n{task}"
        return '["This is a long enough primary post with #AI and enough detail to pass validation."]'

    monkeypatch.setattr("src.agents._sync_generate", _capture_prompt)
    monkeypatch.setattr("src.agents.random.choice", lambda seq: seq[0])

    await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="curator",
        news_items=[{"title": "New Model Launch", "description": "Details", "link": "https://example.com"}],
    )

    assert "PERSONA VARIANT (" in captured["prompt"]


def test_truncate_for_platform_enforces_limit():
    text = "x" * (REPLY_MAX_CHARS + 40)
    assert len(_truncate_for_platform(text, REPLY_MAX_CHARS)) == REPLY_MAX_CHARS


@pytest.mark.asyncio
async def test_handle_interactions_truncates_reply_and_applies_delay(monkeypatch):
    sent_posts = []
    sleep_calls = []
    replied_state = []

    class DummyMention:
        reason = "mention"
        is_read = False
        uri = "at://mention/1"
        cid = "cid-1"

        class record:
            text = "Can you share advice on architecture tradeoffs?"

        class author:
            handle = "alice.example"

    class DummyNotifications:
        notifications = [DummyMention()]

    class DummyNotificationAPI:
        async def list_notifications(self):
            return DummyNotifications()

    class DummyBsky:
        notification = DummyNotificationAPI()

    class DummyApp:
        bsky = DummyBsky()

    class DummyClient:
        app = DummyApp()

        async def send_post(self, text, reply_to):
            sent_posts.append({"text": text, "reply_to": reply_to})

    async def no_sleep(seconds):
        sleep_calls.append(seconds)

    def fake_update_replied_to(mutator):
        nonlocal replied_state
        replied_state = mutator(replied_state)
        return replied_state

    monkeypatch.setattr("src.agents.update_replied_to", fake_update_replied_to)
    monkeypatch.setattr("src.agents.random.random", lambda: 0.95)
    monkeypatch.setattr("src.agents.random.uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr("src.agents.asyncio.sleep", no_sleep)
    monkeypatch.setattr("src.agents._sync_generate", lambda *_args, **_kwargs: "z" * (REPLY_MAX_CHARS + 30))

    await handle_interactions(DummyClient(), "bot.example", "fake-key")

    assert sent_posts
    assert len(sent_posts[0]["text"]) == REPLY_MAX_CHARS
    assert sleep_calls == [0.0]


@pytest.mark.asyncio
async def test_handle_interactions_can_skip_reply_for_human_cadence(monkeypatch):
    sent_posts = []
    replied_state = []

    class DummyMention:
        reason = "mention"
        is_read = False
        uri = "at://mention/skip"
        cid = "cid-skip"

        class record:
            text = "hello"

        class author:
            handle = "bob.example"

    class DummyNotifications:
        notifications = [DummyMention()]

    class DummyNotificationAPI:
        async def list_notifications(self):
            return DummyNotifications()

    class DummyBsky:
        notification = DummyNotificationAPI()

    class DummyApp:
        bsky = DummyBsky()

    class DummyClient:
        app = DummyApp()

        async def send_post(self, text, reply_to):
            sent_posts.append({"text": text, "reply_to": reply_to})

    def fake_update_replied_to(mutator):
        nonlocal replied_state
        replied_state = mutator(replied_state)
        return replied_state

    monkeypatch.setattr("src.agents.update_replied_to", fake_update_replied_to)
    monkeypatch.setattr("src.agents.random.random", lambda: 0.0)

    await handle_interactions(DummyClient(), "bot.example", "fake-key")

    assert sent_posts == []
    assert "at://mention/skip" in replied_state


@pytest.mark.asyncio
async def test_generate_content_returns_empty_when_model_always_returns_non_list(monkeypatch):
    """v4.18: non-list responses exhaust the chain (ValueError → retry → next model
    → all fail) and now return [] instead of a hardcoded placeholder string."""
    monkeypatch.setattr("src.agents._sync_generate", lambda *_args, **_kwargs: '{"not":"a list"}')

    content, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
    )

    assert content == []


def test_sync_generate_reuses_cached_client(monkeypatch):
    call_count = {"count": 0}
    test_cache = {}

    class DummyResponse:
        text = "ok"

    class DummyModels:
        def generate_content(self, model, contents=None, config=None):
            return DummyResponse()

    class DummyClient:
        def __init__(self, api_key):
            call_count["count"] += 1
            self.models = DummyModels()

    monkeypatch.setattr("src.agents._CLIENT_CACHE", test_cache)
    monkeypatch.setattr("src.agents.genai.Client", DummyClient)

    assert _sync_generate("fake-key", "system", "prompt 1", "gemini-2.5-flash") == "ok"
    assert _sync_generate("fake-key", "system", "prompt 2", "gemini-2.5-flash") == "ok"
    assert call_count["count"] == 1


def test_sync_generate_missing_key_raises_clear_error():
    with pytest.raises(ValueError, match="missing or empty"):
        _sync_generate("", "system", "prompt", "gemini-2.5-flash")


def test_sync_generate_invalid_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr("src.agents._CLIENT_CACHE", {})

    def _raise_client_error(*_args, **_kwargs):
        raise RuntimeError("bad credentials")

    monkeypatch.setattr("src.agents.genai.Client", _raise_client_error)

    with pytest.raises(ValueError, match="Failed to initialize Gemini client"):
        _sync_generate("invalid-key", "system", "prompt", "gemini-2.5-flash")


@pytest.mark.asyncio
async def test_model_failover_advances_to_next_model_on_api_error(monkeypatch):
    """An API-level exception on the primary model should cause the next
    model to be tried. v4.18.1 promoted gemini-2.5-pro to primary; this
    test now fails on the primary and succeeds on the next-tier fallback
    (gemini-2.5-flash) to verify the failover still works after the
    re-ordering."""
    models_tried = []

    def _track_and_fail_primary(api_key, system_instr, task, model):
        models_tried.append(model)
        if model == "gemini-2.5-pro":
            raise ConnectionError("quota exceeded")
        return '["This is a long enough post that covers #AI and passes all validation checks."]'

    monkeypatch.setattr("src.agents._sync_generate", _track_and_fail_primary)

    content, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
    )

    assert "gemini-2.5-pro" in models_tried
    assert "gemini-2.5-flash" in models_tried
    assert isinstance(content, list)
    assert len(content) >= 1
    assert "quota exceeded" not in content[0]


@pytest.mark.asyncio
async def test_model_failover_does_not_advance_on_json_error(monkeypatch):
    """A JSON decode error is a content quality issue; the same model should be retried."""
    models_tried = []
    attempt_count = [0]

    def _track_and_return_bad_json(api_key, system_instr, task, model):
        models_tried.append(model)
        attempt_count[0] += 1
        return "not valid json at all"

    monkeypatch.setattr("src.agents._sync_generate", _track_and_return_bad_json)

    content, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
    )

    # Should have retried the same models 2x each — never jumped early due to JSON error
    # All models will be tried (2 attempts each), ending in exhaustion (empty list)
    assert models_tried.count("gemini-2.5-flash") == 2
    assert content == []  # v4.18: exhausted chain returns empty, not placeholder


@pytest.mark.asyncio
async def test_model_used_event_logged_on_success(monkeypatch):
    """A successful generation should log the model_used event."""
    log_events = []

    def _ok_generate(api_key, system_instr, task, model):
        return '["Long enough post with #AI and enough detail to pass all validation checks here."]'

    def _capture_info(event, message="", **fields):
        log_events.append((event, fields))

    monkeypatch.setattr("src.agents._sync_generate", _ok_generate)
    monkeypatch.setattr("src.agents.SafeLogger.info", _capture_info)

    await generate_content(api_key="fake-key", recent_posts=[], mode="mentor")

    assert any(event == "model_used" for event, _ in log_events)
    model_event = next((fields for event, fields in log_events if event == "model_used"), None)
    assert model_event is not None
    assert "model" in model_event


@pytest.mark.asyncio
async def test_generate_content_injects_language_directive(monkeypatch):
    """LANGUAGE directive is present in the prompt sent to Gemini."""
    captured = {}

    def fake_sync_generate(api_key, system_instr, task, model):
        captured["prompt"] = f"{system_instr}\n\n{task}"
        return '["Long enough post with #AI and enough detail to pass all validation checks here."]'

    monkeypatch.setattr("src.agents._sync_generate", fake_sync_generate)

    await generate_content(api_key="fake-key", recent_posts=[], mode="mentor")

    assert "LANGUAGE:" in captured["prompt"]
    assert "English" in captured["prompt"]


# ---------------------------------------------------------------------------
# Curator prompt tuning (2026-05-21) — design-intent sanity tests
# ---------------------------------------------------------------------------
# The Curator prompt was rewritten after honest reading of 25 live posts
# showed paper-summary output ("A new paper maps out X. Sobering read.")
# rather than take-led posts. These tests verify the new design-intent
# phrases survive future edits — they do NOT verify model behaviour,
# which is empirical (next live runs are the validator).


def test_curator_prompt_contains_three_part_structure_rule():
    """The 2026-05-21 rewrite added an explicit THREE-PART STRUCTURE rule."""
    from src.config import SYSTEM_INSTRUCTIONS_CURATOR
    assert "THREE-PART STRUCTURE" in SYSTEM_INSTRUCTIONS_CURATOR
    # Each of the three parts must be named explicitly
    assert "THE HOOK" in SYSTEM_INSTRUCTIONS_CURATOR
    assert "THE SUBSTANCE" in SYSTEM_INSTRUCTIONS_CURATOR
    assert "THE LINK" in SYSTEM_INSTRUCTIONS_CURATOR


def test_curator_prompt_bans_paper_summary_phrasings():
    """Specific bot-tell phrasings observed in the live feed must be named in BANNED PHRASES."""
    from src.config import SYSTEM_INSTRUCTIONS_CURATOR
    p = SYSTEM_INSTRUCTIONS_CURATOR
    assert "BANNED PHRASES" in p
    # The exact failure-case phrasings from 2026-05-21 feed read
    assert "A new paper" in p
    assert "A new study" in p
    assert "A new framework" in p
    assert "Researchers have announced" in p


def test_curator_prompt_bans_editorial_filler_suffixes():
    """The 'Sobering read.' / 'Worth a read.' tic gets explicitly banned."""
    from src.config import SYSTEM_INSTRUCTIONS_CURATOR
    p = SYSTEM_INSTRUCTIONS_CURATOR
    assert "BANNED SUFFIXES" in p
    assert "Sobering read" in p
    assert "Worth a read" in p


def test_curator_prompt_makes_first_person_the_default():
    """'First person when natural' was too soft; now stated as the DEFAULT."""
    from src.config import SYSTEM_INSTRUCTIONS_CURATOR
    p = SYSTEM_INSTRUCTIONS_CURATOR
    assert "FIRST PERSON IS THE DEFAULT" in p


@pytest.mark.asyncio
async def test_generate_post_image_returns_bytes_on_success(monkeypatch):
    """generate_post_image returns raw bytes when _sync_generate_image succeeds."""
    monkeypatch.setattr(agents, "_sync_generate_image", lambda k, p: b"fake-image-bytes")
    # _craft_visual_prompt calls _sync_generate_text — stub it so the test is isolated
    monkeypatch.setattr(agents, "_sync_generate_text", lambda k, s, t: "a glowing sphere")
    result = await generate_post_image("fake-key", "automation")
    assert result == b"fake-image-bytes"


@pytest.mark.asyncio
async def test_generate_post_image_returns_none_on_failure(monkeypatch):
    """generate_post_image returns None gracefully when image generation fails."""
    monkeypatch.setattr(agents, "_sync_generate_text", lambda k, s, t: "a glowing sphere")

    def raise_error(k, p):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(agents, "_sync_generate_image", raise_error)
    result = await generate_post_image("fake-key", "automation")
    assert result is None


@pytest.mark.asyncio
async def test_generate_post_image_uses_crafted_prompt(monkeypatch):
    """When _craft_visual_prompt returns a prompt, that prompt is passed to Imagen."""
    captured_prompt = {}

    monkeypatch.setattr(agents, "_sync_generate_text", lambda k, s, t: "intersecting geometric rings")

    def capture_image(api_key, prompt):
        captured_prompt["value"] = prompt
        return b"img"

    monkeypatch.setattr(agents, "_sync_generate_image", capture_image)
    await generate_post_image("fake-key", "teamwork", thread_posts=["Post one", "Post two"])

    assert captured_prompt["value"] == "intersecting geometric rings"


@pytest.mark.asyncio
async def test_generate_post_image_falls_back_to_static_prompt_on_craft_failure(monkeypatch):
    """When _craft_visual_prompt fails, generate_post_image falls back to the static template."""
    captured_prompt = {}

    def raise_text_error(k, s, t):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(agents, "_sync_generate_text", raise_text_error)

    def capture_image(api_key, prompt):
        captured_prompt["value"] = prompt
        return b"img"

    monkeypatch.setattr(agents, "_sync_generate_image", capture_image)
    await generate_post_image("fake-key", "leadership", thread_posts=["Post one"])

    # Fallback prompt includes the topic name
    assert "leadership" in captured_prompt["value"]
    assert "illustration" in captured_prompt["value"]


# ---------------------------------------------------------------------------
# B3: Gemma prompt adaptation
# ---------------------------------------------------------------------------

def test_build_generate_kwargs_non_gemma_uses_system_instruction():
    """Non-Gemma models should use the config system_instruction parameter."""
    kwargs = _build_generate_kwargs("gemini-2.5-flash", "SYS", "TASK")
    assert kwargs["contents"] == "TASK"
    assert kwargs["config"]["system_instruction"] == "SYS"


def test_build_generate_kwargs_gemma_inlines_system_instruction():
    """Gemma models must have the system prompt inlined — no system_instruction key.

    v4.15.3: config is now always present (carries max_output_tokens cap), but
    Gemma still can't accept system_instruction — it goes in the user turn.
    """
    kwargs = _build_generate_kwargs("gemma-3-27b-it", "SYS", "TASK")
    assert "SYS" in kwargs["contents"]
    assert "TASK" in kwargs["contents"]
    assert "system_instruction" not in kwargs.get("config", {})
    assert "max_output_tokens" in kwargs["config"]


def test_build_generate_kwargs_gemma_detection_is_case_insensitive():
    """Model name casing shouldn't affect Gemma detection."""
    kwargs_lower = _build_generate_kwargs("gemma-3-27b-it", "S", "T")
    kwargs_upper = _build_generate_kwargs("GEMMA-3-27B-IT", "S", "T")
    assert "system_instruction" not in kwargs_lower.get("config", {})
    assert "system_instruction" not in kwargs_upper.get("config", {})


def test_validate_thread_shape_rejects_overlong_post():
    """v4.15.3: a post over MAX_POST_LENGTH_BSKY must hard-reject validation.

    Silent truncation / word-boundary splitting was a bot tell — a
    mid-sentence cut-off halves the credibility of every post that ends
    well. Better to skip the run than ship the tell.
    """
    from src.config import MAX_POST_LENGTH_BSKY
    overlong = "x" * (MAX_POST_LENGTH_BSKY + 1)
    ok, reason = _validate_thread_shape([overlong])
    assert ok is False
    assert "exceeds" in reason


def test_validate_thread_shape_accepts_post_at_limit():
    from src.config import MAX_POST_LENGTH_BSKY
    at_limit = "x" * MAX_POST_LENGTH_BSKY
    ok, _ = _validate_thread_shape([at_limit])
    assert ok is True


# ---------------------------------------------------------------------------
# v4.16 duplicate-content fixes
# ---------------------------------------------------------------------------

def test_extract_style_fingerprints_flags_singly_used_openers():
    """v4.16: count >= 1 (was > 1). The previous threshold required two
    occurrences of the same opener within the window before flagging it,
    which silently skipped the case we actually care about — a pattern
    used once that the LLM is about to reuse on the next run."""
    from src.agents import _extract_style_fingerprints

    posts = [
        "Notes on caching invalidation across three layers.",
        "Working with legacy code is mostly archaeology.",
    ]
    fp = _extract_style_fingerprints(posts)
    # Both openers appear exactly once and must now be flagged.
    assert any("notes on caching" in o for o in fp["repeated_openers"])
    assert any("working with legacy" in o for o in fp["repeated_openers"])


def test_build_avoidance_constraints_includes_recent_post_excerpts():
    """v4.16: concrete excerpts are now included in the prompt as
    'do NOT produce text that is structurally similar' examples — the
    abstract opener-only signal was insufficient."""
    from src.agents import _build_avoidance_constraints

    constraints = _build_avoidance_constraints(
        {"repeated_openers": [], "repeated_hashtags": []},
        recent_posts=[
            "Yesterday's observation about cache invalidation.",
            "An earlier note on monitoring's blind spots.",
        ],
    )
    assert "Recent post excerpts" in constraints
    assert "do NOT produce" in constraints
    assert "cache invalidation" in constraints
    assert "monitoring's blind spots" in constraints


def test_build_avoidance_constraints_truncates_long_excerpts():
    from src.agents import _build_avoidance_constraints

    long_post = "x" * 500
    constraints = _build_avoidance_constraints(
        {"repeated_openers": [], "repeated_hashtags": []},
        recent_posts=[long_post],
    )
    # Each excerpt is capped; the constraint string should not contain
    # the full 500-char post verbatim.
    assert "x" * 500 not in constraints
    assert "…" in constraints


def test_pick_topic_avoiding_recent_skips_recent_picks():
    from src.agents import _pick_topic_avoiding_recent

    candidates = ["Career", "Automation", "Work-Life Balance", "Learning"]
    recent = ["Work-Life Balance"]
    # 100 trials — the recent one must never come up while a fresh option exists.
    picks = {_pick_topic_avoiding_recent(candidates, recent) for _ in range(100)}
    assert "Work-Life Balance" not in picks
    assert picks.issubset(set(candidates))


def test_pick_topic_avoiding_recent_falls_back_when_all_exhausted():
    """When `recent` covers every candidate, fall back to unrestricted choice
    so we never raise mid-run."""
    from src.agents import _pick_topic_avoiding_recent

    candidates = ["A", "B"]
    recent = ["A", "B", "A"]
    pick = _pick_topic_avoiding_recent(candidates, recent)
    assert pick in candidates


@pytest.mark.asyncio
async def test_generate_content_avoids_recent_mode_topics_in_mentor(monkeypatch):
    """End-to-end: passing recent_mode_topics covering 3 of 4 Mentor topics
    must force the picker to land on the remaining one."""
    captured = {"task": ""}

    def _capture_prompt(_api_key, _system_instr, task, _model):
        captured["task"] = task
        return '["A real Mentor post that meets the validator length floor with no banned hype."]'

    monkeypatch.setattr("src.agents._sync_generate", _capture_prompt)

    # v4.18.1: MENTOR_TOPICS expanded from 4 to 12. Blocking 3 no longer
    # pins the pick to a single value — instead, assert the picker never
    # lands on a blocked one when fresh options exist.
    blocked = ["Career", "Automation", "Work-Life Balance"]
    await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
        recent_mode_topics=blocked,
    )
    # Extract the topic from the task — format is "TOPIC: <topic>\n\n"
    import re as _re
    match = _re.search(r"TOPIC:\s*(.+?)\n", captured["task"])
    assert match, f"Could not find TOPIC line in task: {captured['task'][:200]}"
    picked = match.group(1).strip()
    assert picked not in blocked, f"Picker landed on a blocked topic: {picked}"


# ---------------------------------------------------------------------------
# v4.19 Pioneer URL-required validator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pioneer_post_missing_required_url_is_rejected(monkeypatch):
    """If the pioneer entry has a `link`, the post MUST contain that URL.
    A post that omits the URL must trigger the retry path (raises ValueError
    inside the rescue loop, surfaces as content_generation_attempt_failed)."""
    required_url = "https://example.com/canonical-source"
    attempts = []

    def _model_omits_url(_api_key, _system_instr, task, _model):
        attempts.append(task)
        # Realistic-length post that passes shape + voice validators but
        # deliberately omits the URL the entry requires.
        return '["A perfectly reasonable observation about the historical event in question that lands on a statement and meets the minimum-length floor."]'

    monkeypatch.setattr("src.agents._sync_generate", _model_omits_url)

    pioneer_entry = {
        "pool": "undated",
        "entry": {
            "id": "fake-pioneer",
            "title": "A fake pioneer fact",
            "detail": "Some detail that does not include any URL.",
            "link": required_url,
        },
    }

    content, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
        pioneer_entry=pioneer_entry,
    )

    # Model never included the URL → all attempts rejected → chain exhausts
    # → returns [] (per v4.18 catastrophic-fallback removal).
    assert content == []
    # And the prompt-builder did include the required URL in the task:
    assert required_url in attempts[0]


@pytest.mark.asyncio
async def test_pioneer_post_with_required_url_is_accepted(monkeypatch):
    """Counter-test: same setup but the model includes the URL → accepted."""
    required_url = "https://example.com/canonical-source"

    def _model_includes_url(_api_key, _system_instr, _task, _model):
        return (
            '["A perfectly reasonable observation about the historical event '
            'in question that lands on a statement and meets the minimum-length '
            f'floor.\\n\\n{required_url}"]'
        )

    monkeypatch.setattr("src.agents._sync_generate", _model_includes_url)

    pioneer_entry = {
        "pool": "undated",
        "entry": {
            "id": "fake-pioneer",
            "title": "A fake pioneer fact",
            "detail": "Some detail.",
            "link": required_url,
        },
    }

    content, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
        pioneer_entry=pioneer_entry,
    )

    assert len(content) == 1
    assert required_url in content[0]


@pytest.mark.asyncio
async def test_pioneer_post_without_link_field_skips_url_check(monkeypatch):
    """Entries without a `link` field are exempt — the validator only fires
    when the entry actually has a URL. A post without a URL should pass."""

    def _model_returns_plain_post(_api_key, _system_instr, _task, _model):
        return '["A perfectly reasonable observation about the historical event in question that lands on a statement and meets the minimum-length floor."]'

    monkeypatch.setattr("src.agents._sync_generate", _model_returns_plain_post)

    pioneer_entry = {
        "pool": "undated",
        "entry": {
            "id": "fake-pioneer-no-link",
            "title": "A fact with no source URL",
            "detail": "Some detail.",
            # no "link" key
        },
    }

    content, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
        pioneer_entry=pioneer_entry,
    )

    assert len(content) == 1
