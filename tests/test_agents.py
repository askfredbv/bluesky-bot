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

    content, topic, _ = await generate_content(
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


@pytest.mark.asyncio
async def test_curator_link_follows_chosen_item(monkeypatch):
    """v4.21: Curator returns {"url", "posts"}; generate_content resolves the
    chosen URL so topic + chosen_link follow the item the model actually wrote
    about, not the top-scored news_items[0]. This fixes the text/link-card
    mismatch where the prompt invites picking a non-top item but the card was
    locked to the top item."""
    news_items = [
        {"title": "Top Scored Item", "description": "d0", "link": "https://example.com/top"},
        {"title": "The One I Wrote About", "description": "d1", "link": "https://example.com/chosen"},
    ]

    def _pick_second(_api_key, _system_instr, _task, _model):
        return (
            '{"url": "https://example.com/chosen", '
            '"posts": ["I keep seeing this exact failure mode in production and the '
            '#AI tooling still does not catch it before it ships."]}'
        )

    monkeypatch.setattr("src.agents._sync_generate", _pick_second)

    content, topic, chosen_link = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="curator",
        news_items=news_items,
    )

    assert len(content) == 1
    assert topic == "The One I Wrote About"
    assert chosen_link == "https://example.com/chosen"


@pytest.mark.asyncio
async def test_curator_unmatched_url_falls_back_to_top_item(monkeypatch):
    """If the model returns a URL not among the offered items (hallucinated or
    edited), generate_content falls back to the top-scored item so the run
    still ships a coherent post + card rather than skipping."""
    news_items = [
        {"title": "Top Scored Item", "description": "d0", "link": "https://example.com/top"},
        {"title": "Second", "description": "d1", "link": "https://example.com/second"},
    ]

    def _hallucinate_url(_api_key, _system_instr, _task, _model):
        return (
            '{"url": "https://example.com/not-in-the-list", '
            '"posts": ["I keep seeing this exact failure mode in production and the '
            '#AI tooling still does not catch it before it ships."]}'
        )

    monkeypatch.setattr("src.agents._sync_generate", _hallucinate_url)

    content, topic, chosen_link = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="curator",
        news_items=news_items,
    )

    assert len(content) == 1
    assert topic == "Top Scored Item"
    assert chosen_link == "https://example.com/top"


@pytest.mark.asyncio
async def test_curator_missing_url_retries_does_not_fall_back(monkeypatch):
    """A curator response with valid `posts` but NO `url` must NOT be accepted.

    Regression guard for the Codex PR #51 review: a missing url left
    chosen_link None and silently fell back to news_items[0], re-introducing
    the text/card mismatch this PR exists to kill. The url is now required, so
    a response without it raises → retries → (if never supplied) the run skips
    cleanly per v4.18, rather than shipping a post whose card points at the
    top-scored item the text may not be about."""
    news_items = [
        {"title": "Top Scored Item", "description": "d0", "link": "https://example.com/top"},
        {"title": "Second", "description": "d1", "link": "https://example.com/second"},
    ]

    def _posts_without_url(_api_key, _system_instr, _task, _model):
        return (
            '{"posts": ["I keep seeing this exact failure mode in production and '
            'the #AI tooling still does not catch it before it ships."]}'
        )

    monkeypatch.setattr("src.agents._sync_generate", _posts_without_url)

    content, _topic, chosen_link = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="curator",
        news_items=news_items,
    )

    # Skipped, not silently shipped against the top item's card.
    assert content == []
    assert chosen_link is None


@pytest.mark.asyncio
async def test_curator_recovers_when_retry_supplies_url(monkeypatch):
    """If the first attempt omits `url` but a later attempt supplies a valid
    one, the run recovers and ships against the chosen item — proving the
    required-url check triggers a retry rather than a hard skip."""
    news_items = [
        {"title": "Top Scored Item", "description": "d0", "link": "https://example.com/top"},
        {"title": "The One I Wrote About", "description": "d1", "link": "https://example.com/chosen"},
    ]
    calls = {"n": 0}

    def _no_url_then_url(_api_key, _system_instr, _task, _model):
        calls["n"] += 1
        if calls["n"] == 1:
            return (
                '{"posts": ["I keep seeing this exact failure mode in production '
                'and the #AI tooling still does not catch it before it ships."]}'
            )
        return (
            '{"url": "https://example.com/chosen", '
            '"posts": ["I keep seeing this exact failure mode in production and '
            'the #AI tooling still does not catch it before it ships."]}'
        )

    monkeypatch.setattr("src.agents._sync_generate", _no_url_then_url)

    content, topic, chosen_link = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="curator",
        news_items=news_items,
    )

    assert calls["n"] >= 2  # retried after the missing-url attempt
    assert len(content) == 1
    assert topic == "The One I Wrote About"
    assert chosen_link == "https://example.com/chosen"


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

    content, _, _ = await generate_content(
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
    model to be tried. 2026-08-18 promoted gemini-3.7-flash to primary (voice
    trial); this test fails on the primary (3.7-flash) and succeeds on the
    immediate fallback (gemini-3.5-flash) to verify the failover still works
    after the re-ordering."""
    models_tried = []

    def _track_and_fail_primary(api_key, system_instr, task, model):
        models_tried.append(model)
        if model == "gemini-3.7-flash":
            raise ConnectionError("quota exceeded")
        return '["This is a long enough post that covers #AI and passes all validation checks."]'

    monkeypatch.setattr("src.agents._sync_generate", _track_and_fail_primary)

    content, _, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
    )

    assert "gemini-3.7-flash" in models_tried
    assert "gemini-3.5-flash" in models_tried
    assert isinstance(content, list)
    assert len(content) >= 1
    assert "quota exceeded" not in content[0]


def test_thinking_budget_for_covers_model_families():
    """Guards the empty-output protection (the 2026-05-11 bug): 2.5-pro and the
    whole 2.5 / 3.x-flash line must pin a thinking budget, while 1.5-flash —
    which has no thinking mode — must stay None (sending thinking_config would
    error and break that fallback). The failover test stubs _sync_generate, so
    this is the only place the regex branch is actually exercised."""
    from src.agents import _thinking_budget_for as budget

    # The Gemini 3.x flash line (matched by regex) all disable thinking.
    assert budget("gemini-3.7-flash") == 0
    assert budget("gemini-3.6-flash") == 0
    assert budget("gemini-3.5-flash") == 0
    # 2.5 family: flash disables, pro pins to its minimum (cannot fully disable).
    assert budget("gemini-2.5-flash") == 0
    assert budget("gemini-2.5-pro") == 128
    # No thinking mode → must NOT send thinking_config (return None).
    assert budget("gemini-1.5-flash-latest") is None
    assert budget("gemma-3-27b-it") is None


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

    content, _, _ = await generate_content(
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


def test_curator_prompt_warns_against_template_openings():
    """2026-06-12 Gemini voice audit: external-content posts over-used the
    'The most interesting bit in this paper' / 'The bit that landed' opener.
    The prompt now flags template-repetition as the AI tell."""
    from src.config import SYSTEM_INSTRUCTIONS_CURATOR
    p = SYSTEM_INSTRUCTIONS_CURATOR
    assert "VARY THE OPENING" in p
    assert "the template is the tell" in p.lower()


# ---------------------------------------------------------------------------
# Voice anchor (2026-05-21) — STYLE_GUIDELINES + structural moves
# ---------------------------------------------------------------------------
# Verifies the voice-anchor commit's design-intent phrases survive future
# edits. STYLE_GUIDELINES is injected into Curator / Mentor / Pioneer /
# Strategist prompts via f-string interpolation, so changes here affect
# every post the bot writes — these tests are the tripwire if someone
# accidentally drops a register or move from the canon.


def test_style_guidelines_contains_both_voice_registers():
    """Strategic-advisory + casual-narrative both named explicitly."""
    from src.config import STYLE_GUIDELINES
    assert "REGISTER A" in STYLE_GUIDELINES
    assert "REGISTER B" in STYLE_GUIDELINES
    assert "STRATEGIC ADVISORY" in STYLE_GUIDELINES
    assert "CASUAL NARRATIVE" in STYLE_GUIDELINES


def test_style_guidelines_contains_anchor_quotes_from_real_posts():
    """Verbatim quotes from askfred.be (advisory) + frederikvanhecke.com (casual)."""
    from src.config import STYLE_GUIDELINES
    s = STYLE_GUIDELINES
    # Advisory register (askfred.be)
    assert "The tool rarely fails. Adoption fails." in s
    assert "If your position exists only in your head, theirs will be on paper." in s
    # Casual register (frederikvanhecke.com)
    assert "That's it. I've had it with gravity." in s


def test_style_guidelines_describes_recurring_patterns():
    """STYLE_GUIDELINES still describes observable patterns — just without my analytical labels.

    Earlier iteration named patterns ("KICKER SENTENCE", "CONCESSION-THEN-PIVOT")
    that read as my framework rather than Frederik's. Labels removed
    2026-05-21; the descriptive PATTERNS section remained with verbatim
    anchor quotes so the model still has shapes to imitate.
    """
    from src.config import STYLE_GUIDELINES
    s = STYLE_GUIDELINES
    assert "PATTERNS" in s
    # Verbatim anchor quotes from each named-but-now-unlabelled pattern
    # remain (these prove the *content* survived, even after labels went).
    assert "The tool rarely fails. Adoption fails." in s
    assert "Technology is the easy part of digital transformation" in s
    assert "Either AI is going to replace professional services" in s


def test_style_guidelines_contractions_rule_is_register_dependent():
    """The old 'no contractions' rule was wrong — it's register-dependent."""
    from src.config import STYLE_GUIDELINES
    s = STYLE_GUIDELINES
    assert "CONTRACTIONS" in s
    # The fix specifically: avoid in advisory, fine in casual
    assert "avoid contractions" in s.lower()
    assert "contractions are fine" in s.lower()


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
    """When _craft_visual_prompt returns a prompt, that prompt is passed to the image model."""
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

    # Fallback prompt includes the topic name and the static template wording
    assert "leadership" in captured_prompt["value"]
    assert "illustration" in captured_prompt["value"]


def _fake_image_response(image_bytes: bytes = b"PNG", *, text_only: bool = False):
    """Duck-typed stand-in for a genai generate_content image response."""
    from types import SimpleNamespace

    if text_only:
        part = SimpleNamespace(inline_data=None, text="refused")
    else:
        part = SimpleNamespace(
            inline_data=SimpleNamespace(data=image_bytes, mime_type="image/png"),
            text=None,
        )
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


class _FakeImageClient:
    def __init__(self, response):
        self._response = response
        self.calls = []
        self.models = self  # so client.models.generate_content resolves here

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def test_sync_generate_image_extracts_inline_bytes(monkeypatch):
    """Migration guard (2026-06-15): pull image bytes from the inline-data Part
    of a gemini-3.1-flash-image generate_content response, not generate_images.
    """
    from src.agents import _sync_generate_image

    client = _FakeImageClient(_fake_image_response(b"\x89PNG-real-bytes"))
    monkeypatch.setitem(agents._CLIENT_CACHE, "img-key", client)

    out = _sync_generate_image("img-key", "a flat editorial illustration")

    assert out == b"\x89PNG-real-bytes"
    assert client.calls, "generate_content was not called"
    assert client.calls[0]["model"] == agents.IMAGE_MODEL


def test_sync_generate_image_returns_none_when_no_image_part(monkeypatch):
    """A text-only response (e.g. a content-filter refusal) yields None, not a crash."""
    from src.agents import _sync_generate_image

    client = _FakeImageClient(_fake_image_response(text_only=True))
    monkeypatch.setitem(agents._CLIENT_CACHE, "img-key2", client)

    assert _sync_generate_image("img-key2", "prompt") is None


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

    content, _, _ = await generate_content(
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

    content, _, _ = await generate_content(
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

    content, _, _ = await generate_content(
        api_key="fake-key",
        recent_posts=[],
        mode="mentor",
        pioneer_entry=pioneer_entry,
    )

    assert len(content) == 1
