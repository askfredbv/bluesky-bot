import asyncio
import pytest

from atproto import models
from src.config import MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON
from src import broadcasters


@pytest.mark.asyncio
async def test_post_to_bluesky_skips_broadcast_on_overlong_content(monkeypatch):
    """v4.15.3: overlong posts are an upstream invariant failure — skip the
    platform's broadcast and log an error. Missing one run beats posting a
    word-boundary-truncated bot tell.
    """
    sent_payloads = []
    errors = []

    class DummyPost:
        def __init__(self, idx):
            self.cid = f"cid-{idx}"
            self.uri = f"at://post/{idx}"

    class DummyAsyncClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            sent_payloads.append({"text": text})
            return DummyPost(len(sent_payloads))

    async def no_sleep(_):
        return None

    def capture_error(event, message="", **fields):
        errors.append((event, message, fields))

    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(broadcasters.SafeLogger, "error", capture_error)

    overlong = "x" * (MAX_POST_LENGTH_BSKY + 25)
    dummy_client = DummyAsyncClient()
    await broadcasters.post_to_bluesky(dummy_client, [overlong])

    assert sent_payloads == []  # nothing posted
    assert any(event == "broadcast_invariant_violated" for event, _, _ in errors)


@pytest.mark.asyncio
async def test_post_to_mastodon_skips_broadcast_on_overlong_content(monkeypatch):
    """v4.15.3: overlong Mastodon posts also trigger the invariant skip."""
    posted_statuses = []
    errors = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            self.access_token = access_token

        def status_post(self, status, in_reply_to_id, visibility, idempotency_key=None):
            posted_statuses.append({"status": status})
            return {"id": len(posted_statuses)}

    async def no_sleep(_):
        return None

    def capture_error(event, message="", **fields):
        errors.append((event, message, fields))

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(broadcasters.SafeLogger, "error", capture_error)

    overlong = "y" * (MAX_POST_LENGTH_MASTODON + 40)
    await broadcasters.post_to_mastodon("token", "https://mastodon.example", [overlong])

    assert posted_statuses == []
    assert any(event == "broadcast_invariant_violated" for event, _, _ in errors)


@pytest.mark.asyncio
async def test_post_to_mastodon_cancellation_stops_after_current_post(monkeypatch):
    posted_statuses = []
    sleep_started = asyncio.Event()

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            self.access_token = access_token
            self.api_base_url = api_base_url

        def status_post(self, status, in_reply_to_id, visibility, idempotency_key=None):
            posted_statuses.append(
                {
                    "status": status,
                    "in_reply_to_id": in_reply_to_id,
                    "visibility": visibility,
                }
            )
            return {"id": len(posted_statuses)}

    async def blocking_sleep(_):
        sleep_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", blocking_sleep)

    task = asyncio.create_task(
        broadcasters.post_to_mastodon(
            "token",
            "https://mastodon.example",
            ["post one", "post two"],
            thread_pause_profile="quick",
        )
    )
    await asyncio.wait_for(sleep_started.wait(), timeout=1.0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(posted_statuses) == 1


def test_sample_thread_pause_respects_profile_ranges():
    for profile_name, (low, high) in broadcasters.THREAD_PAUSE_PROFILES.items():
        for _ in range(25):
            value = broadcasters._sample_thread_pause(profile_name)
            assert low <= value <= high


def test_sample_thread_pause_falls_back_to_default_profile_range():
    low, high = broadcasters.THREAD_PAUSE_PROFILES[broadcasters.DEFAULT_THREAD_PAUSE_PROFILE]
    for _ in range(25):
        value = broadcasters._sample_thread_pause("nonexistent-profile")
        assert low <= value <= high


@pytest.mark.asyncio
async def test_post_to_bluesky_uses_image_embed_when_image_bytes_provided(monkeypatch):
    """When image_bytes are supplied, an AppBskyEmbedImages.Main embed is attached to the first post."""
    send_post_calls = []

    fake_blob = models.blob_ref.BlobRef(ref={"link": "bafkreiaa"}, mime_type="image/png", size=3)

    class FakeUploadResult:
        blob = fake_blob

    class DummyAsyncClient:
        upload_blob_call_count = 0

        async def upload_blob(self, data):
            self.upload_blob_call_count += 1
            return FakeUploadResult()

        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            send_post_calls.append({"text": text, "embed": embed})

            class FakePost:
                cid = "cid-1"
                uri = "at://post/1"
            return FakePost()

    monkeypatch.setattr(broadcasters.asyncio, "sleep", lambda _: None)

    dummy_client = DummyAsyncClient()
    await broadcasters.post_to_bluesky(dummy_client, ["Post text"], image_bytes=b"img-data")

    assert dummy_client.upload_blob_call_count == 1
    assert isinstance(send_post_calls[0]["embed"], models.AppBskyEmbedImages.Main)


# ---------------------------------------------------------------------------
# Image compression (2026-06-14 fix): Imagen 4 output clusters around/above
# the 976 KB Bluesky gate, so compress-to-fit instead of measure-and-drop.
# ---------------------------------------------------------------------------

def _make_oversized_png(side: int = 600) -> bytes:
    """A noise RGB PNG that mimics Imagen-4 output: large as PNG (~1 MB,
    over the 976 KB gate), much smaller as JPEG. Noise is incompressible for
    PNG but lossy-JPEG shrinks it ~4x — the same shape as the real bug."""
    import io as _io
    import os as _os
    from PIL import Image
    img = Image.frombytes("RGB", (side, side), _os.urandom(side * side * 3))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compress_passthrough_when_already_small():
    data = b"tiny-image-bytes"
    out, fits = broadcasters.compress_image_to_fit(data, 1024)
    assert out is data
    assert fits is True


def test_compress_reencodes_oversized_image_under_the_real_gate():
    png = _make_oversized_png(600)
    assert len(png) > broadcasters._BLUESKY_IMAGE_MAX_BYTES  # over the real gate
    out, fits = broadcasters.compress_image_to_fit(png, broadcasters._BLUESKY_IMAGE_MAX_BYTES)
    assert fits is True
    assert len(out) <= broadcasters._BLUESKY_IMAGE_MAX_BYTES
    assert out is not png  # actually re-encoded, not passed through


def test_compress_returns_false_when_no_budget_can_fit():
    png = _make_oversized_png(200)
    out, fits = broadcasters.compress_image_to_fit(png, 10)
    assert fits is False
    assert out is png  # nothing usable produced; caller will skip the attach


def test_compress_failure_logs_reason_distinctly(monkeypatch):
    """A real compression FAILURE (un-openable bytes) logs image_compress_failed
    with error_msg — distinct from a genuine 'too large'. Codex PR #56 finding:
    don't let a processing error masquerade as a size problem and swallow the
    exception the repo relies on for diagnosing image outages."""
    events = []
    monkeypatch.setattr(broadcasters.SafeLogger, "warn",
                        lambda event, message="", **fields: events.append((event, fields)))

    garbage = b"this is not a valid image " * 100  # > budget, but Pillow can't open it
    out, fits = broadcasters.compress_image_to_fit(garbage, 100)

    assert fits is False
    assert out is garbage
    names = [e for e, _ in events]
    assert "image_compress_failed" in names
    fields = next(f for e, f in events if e == "image_compress_failed")
    assert "error_msg" in fields and fields["error_msg"]  # the real reason is captured


@pytest.mark.asyncio
async def test_post_to_bluesky_compresses_oversized_image_instead_of_dropping(monkeypatch):
    """An over-the-gate image is compressed to fit and ATTACHED — not silently
    dropped as before. Regression guard for the 2026-06-14 image-drought bug.
    Uses the REAL 976 KB gate with a ~1 MB image, exactly like production."""
    png = _make_oversized_png(600)
    assert len(png) > broadcasters._BLUESKY_IMAGE_MAX_BYTES

    uploaded = {}
    fake_blob = models.blob_ref.BlobRef(ref={"link": "bafkreiaa"}, mime_type="image/jpeg", size=3)

    class FakeUploadResult:
        blob = fake_blob

    class DummyAsyncClient:
        async def upload_blob(self, data):
            uploaded["bytes"] = data
            return FakeUploadResult()

        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            class FakePost:
                cid = "cid-1"
                uri = "at://post/1"
            return FakePost()

    monkeypatch.setattr(broadcasters.asyncio, "sleep", lambda _: None)
    info_events = []
    monkeypatch.setattr(broadcasters.SafeLogger, "info",
                        lambda event, message="", **fields: info_events.append(event))

    await broadcasters.post_to_bluesky(DummyAsyncClient(), ["Post text"], image_bytes=png)

    assert "bytes" in uploaded  # image was uploaded, not dropped
    assert len(uploaded["bytes"]) <= broadcasters._BLUESKY_IMAGE_MAX_BYTES
    assert "image_attached" in info_events


@pytest.mark.asyncio
async def test_post_to_bluesky_uses_link_card_when_no_image_bytes(monkeypatch):
    """When only link_meta is supplied (no image_bytes), an AppBskyEmbedExternal.Main embed is attached."""
    send_post_calls = []

    class DummyAsyncClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            send_post_calls.append({"text": text, "embed": embed})

            class FakePost:
                cid = "cid-1"
                uri = "at://post/1"
            return FakePost()

    monkeypatch.setattr(broadcasters.asyncio, "sleep", lambda _: None)

    link_meta = {"title": "Title", "description": "Desc", "url": "https://example.com"}
    dummy_client = DummyAsyncClient()
    await broadcasters.post_to_bluesky(dummy_client, ["Post text"], link_meta=link_meta)

    assert isinstance(send_post_calls[0]["embed"], models.AppBskyEmbedExternal.Main)


@pytest.mark.asyncio
async def test_post_to_mastodon_attaches_image_to_first_post_only(monkeypatch):
    """When image_bytes is provided, media is uploaded and attached to the root post."""
    posted = []
    media_posted = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def media_post(self, data, mime_type=None, description=None):
            media_posted.append({"data": data, "mime_type": mime_type, "description": description})
            return {"id": "media-42"}

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None, idempotency_key=None):
            posted.append({"status": status, "reply": in_reply_to_id, "media_ids": media_ids})
            return {"id": len(posted)}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example",
        ["root post", "reply post"],
        image_bytes=b"\x89PNG\r\n\x1a\n" + b"x" * 100,  # minimal PNG-ish bytes
    )

    assert len(media_posted) == 1
    assert media_posted[0]["description"].startswith("Illustration:")
    assert posted[0]["media_ids"] == ["media-42"]  # first post only
    assert posted[1]["media_ids"] is None           # reply gets no media


@pytest.mark.asyncio
async def test_post_to_mastodon_continues_without_image_on_upload_failure(monkeypatch):
    """If media_post raises, posting continues without the image rather than crashing."""
    posted = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def media_post(self, data, mime_type=None, description=None):
            raise RuntimeError("mastodon rejected upload")

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None, idempotency_key=None):
            posted.append({"status": status, "media_ids": media_ids})
            return {"id": len(posted)}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example",
        ["only post"],
        image_bytes=b"bad-bytes",
    )

    assert len(posted) == 1
    assert posted[0]["media_ids"] is None


@pytest.mark.asyncio
async def test_post_to_mastodon_no_image_bytes_skips_media_upload(monkeypatch):
    """When image_bytes is None (Curator mode), media_post is never called."""
    media_calls = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def media_post(self, *a, **kw):
            media_calls.append(True)
            return {"id": "nope"}

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None, idempotency_key=None):
            return {"id": 1}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example",
        ["curator post"],
    )

    assert media_calls == []


# ---------------------------------------------------------------------------
# BroadcastResult return shape (Phase 1 Step 3a)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_to_bluesky_returns_broadcast_result_with_sent_uris(monkeypatch):
    """Successful multi-post thread returns a BroadcastResult whose sent_uris
    lists every post.uri in order, and whose client is the same instance passed in."""
    from src.metrics import BroadcastResult

    uri_counter = {"n": 0}

    class DummyAsyncClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            uri_counter["n"] += 1

            class FakePost:
                cid = f"cid-{uri_counter['n']}"
                uri = f"at://post/{uri_counter['n']}"
            return FakePost()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    dummy_client = DummyAsyncClient()
    result = await broadcasters.post_to_bluesky(dummy_client, ["first", "second", "third"])

    assert isinstance(result, BroadcastResult)
    assert result.client is dummy_client
    assert result.sent_uris == ["at://post/1", "at://post/2", "at://post/3"]
    assert result.error is None


@pytest.mark.asyncio
async def test_post_to_mastodon_returns_broadcast_result_with_sent_ids(monkeypatch):
    """Successful Mastodon thread returns a BroadcastResult whose sent_uris
    carries the status IDs as strings."""
    from src.metrics import BroadcastResult

    id_counter = {"n": 0}

    class DummyMastodon:
        def __init__(self, *a, **kw):
            pass

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None, idempotency_key=None):
            id_counter["n"] += 1
            return {"id": id_counter["n"] * 1000}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    result = await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example",
        ["a", "b"],
    )

    assert isinstance(result, BroadcastResult)
    assert result.client is None
    assert result.sent_uris == ["1000", "2000"]
    assert result.error is None


@pytest.mark.asyncio
async def test_post_to_bluesky_invariant_skip_returns_empty_broadcast_result(monkeypatch):
    """Overlong content → BroadcastResult with empty sent_uris but client preserved."""
    from src.metrics import BroadcastResult

    class DummyAsyncClient:
        async def send_post(self, *a, **kw):
            raise AssertionError("send_post should not be called when invariant fails")

    dummy_client = DummyAsyncClient()
    too_long = "x" * (MAX_POST_LENGTH_BSKY + 1)
    result = await broadcasters.post_to_bluesky(dummy_client, [too_long])

    assert isinstance(result, BroadcastResult)
    assert result.client is dummy_client
    assert result.sent_uris == []


@pytest.mark.asyncio
async def test_post_to_mastodon_no_token_returns_empty_broadcast_result():
    """Missing access token → empty BroadcastResult (caller treats the same as a skip)."""
    from src.metrics import BroadcastResult

    result = await broadcasters.post_to_mastodon("", "https://mastodon.example", ["hi"])

    assert isinstance(result, BroadcastResult)
    assert result.sent_uris == []
    assert result.client is None



# ---------------------------------------------------------------------------
# Mastodon timeout + retry (freeze audit X4, found by Codex).
#
# asyncio.to_thread runs status_post on a worker thread, and Python threads are
# not cancellable: when wait_for times out it abandons the await while the HTTP
# request carries on and may still be accepted. classify_retry calls TimeoutError
# transient, so _send_with_thread_retry sends the same status again. Without
# Mastodon's idempotency_key that is a real duplicate on the timeline.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mastodon_retry_after_timeout_reuses_the_idempotency_key(monkeypatch):
    """A retried post must carry the SAME key, so Mastodon returns the original
    status instead of creating a second one."""
    calls = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def status_post(self, status, in_reply_to_id, visibility, idempotency_key=None, **_kw):
            calls.append({"status": status, "idempotency_key": idempotency_key})
            if len(calls) == 1:
                raise TimeoutError("mastodon did not answer in time")
            return {"id": 101}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    result = await broadcasters.post_to_mastodon("token", "https://mastodon.example", ["One post"])

    assert len(calls) == 2, "the timeout should have been retried"
    assert calls[0]["idempotency_key"], "the first attempt must carry a key"
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]
    assert result.sent_uris == ["101"]


@pytest.mark.asyncio
async def test_mastodon_thread_parts_get_distinct_idempotency_keys(monkeypatch):
    """Each part needs its own key -- one shared key would make Mastodon collapse
    the whole thread into a single status."""
    keys = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def status_post(self, status, in_reply_to_id, visibility, idempotency_key=None, **_kw):
            keys.append(idempotency_key)
            return {"id": len(keys)}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example", ["Part one", "Part two", "Part three"]
    )

    assert len(keys) == 3
    assert all(keys), "every part must carry a key"
    assert len(set(keys)) == 3, "keys must be distinct per part"


@pytest.mark.asyncio
async def test_mastodon_idempotent_retry_does_not_double_count_delivery(monkeypatch):
    """When the retry returns the status the first attempt actually created, the
    thread records one delivery -- not two -- and tracks the real id."""
    attempts = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def status_post(self, status, in_reply_to_id, visibility, idempotency_key=None, **_kw):
            attempts.append(idempotency_key)
            if len(attempts) == 1:
                # The request was accepted server-side; the client gave up waiting.
                raise TimeoutError("timed out after the server accepted it")
            # Mastodon replays the original status for a repeated key.
            return {"id": 555}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    result = await broadcasters.post_to_mastodon("token", "https://mastodon.example", ["Only post"])

    assert result.sent_uris == ["555"]
    assert result.delivered_texts == ["Only post"]
    assert result.error is None


# ---------------------------------------------------------------------------
# Mastodon thread numbering (2026-09-10).
#
# Mastodon lists a self-thread newest-first, so a timeline reader met part 2
# ("Continued thread") before part 1. Bluesky's client already labels
# self-threads "1/2", "2/2"; Mastodon readers got nothing. The broadcaster now
# numbers Mastodon parts, before the discovery tags are appended.
# ---------------------------------------------------------------------------


def test_each_part_of_a_thread_is_labelled():
    assert broadcasters.number_mastodon_thread(["First.", "Second."]) == ["First. 1/2", "Second. 2/2"]
    assert broadcasters.number_mastodon_thread(["a", "b", "c"]) == ["a 1/3", "b 2/3", "c 3/3"]


def test_a_single_post_is_not_labelled():
    """A '1/1' label would be noise; most posts are single."""
    assert broadcasters.number_mastodon_thread(["Only post."]) == ["Only post."]
    assert broadcasters.number_mastodon_thread([]) == []


def test_numbering_is_all_or_nothing_when_a_part_would_overflow():
    """One part missing its label reads worse than none having one."""
    parts = ["short", "x" * 498]
    assert broadcasters.number_mastodon_thread(parts, max_length=500) == parts


def test_the_marker_sits_with_the_prose_and_the_tags_stay_their_own_paragraph():
    """Numbering runs first. The tags must remain a hashtag-only final paragraph,
    or Mastodon's web client stops lifting them into its hashtag bar."""
    numbered = broadcasters.number_mastodon_thread(["They are looking for cover.", "Part two."])
    tagged = broadcasters.apply_mastodon_tags(numbered, ["#Management", "#CorporateCulture"])

    assert tagged[0] == "They are looking for cover. 1/2\n\n#Management #CorporateCulture"
    assert tagged[1] == "Part two. 2/2"


@pytest.mark.asyncio
async def test_post_to_mastodon_sends_numbered_parts_with_tags_after_the_marker(monkeypatch):
    sent = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None, idempotency_key=None):
            sent.append({"status": status, "in_reply_to_id": in_reply_to_id})
            return {"id": str(len(sent))}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    result = await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example",
        ["When a stakeholder asks for an options paper, they want cover.",
         "The document exists to provide the audit trail."],
        tags=["#Management", "#CorporateCulture"],
    )

    assert [s["status"] for s in sent] == [
        "When a stakeholder asks for an options paper, they want cover. 1/2\n\n#Management #CorporateCulture",
        "The document exists to provide the audit trail. 2/2",
    ]
    assert sent[1]["in_reply_to_id"] == "1", "part 2 must still thread under part 1"
    # The metrics rows read delivered_texts, so they must record what was actually sent.
    assert result.delivered_texts == [s["status"] for s in sent]


@pytest.mark.asyncio
async def test_a_single_mastodon_post_ships_without_a_label(monkeypatch):
    sent = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None, idempotency_key=None):
            sent.append(status)
            return {"id": "1"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    await broadcasters.post_to_mastodon("token", "https://mastodon.example", ["Only post."], tags=["#AI"])

    assert sent == ["Only post.\n\n#AI"]


@pytest.mark.asyncio
async def test_the_bluesky_copy_of_a_thread_is_not_numbered(monkeypatch):
    """Bluesky's client already labels self-threads. Adding our own would read
    '1/2 1/2' there; the mechanic is Mastodon-only by design (AGENTS.md #7)."""
    sent = []

    class FakePost:
        cid = "bafyreid"
        uri = "at://did:plc:test/app.bsky.feed.post/1"

    class DummyAsyncClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            sent.append(text)
            return FakePost()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    await broadcasters.post_to_bluesky(DummyAsyncClient(), ["Part one.", "Part two."])

    assert sent == ["Part one.", "Part two."]


# ---------------------------------------------------------------------------
# Mastodon source link (freeze audit X6, 2026-09-10).
#
# Bluesky shows a Curator post's source as a link card; Mastodon only shows a
# link that is in the text, and the model usually leaves it out. 4 of the last
# 5 Curator posts reached Mastodon with no source at all.
# ---------------------------------------------------------------------------

P = chr(10) * 2  # a paragraph break, spelled without an escape sequence
SRC = "https://www.theregister.com/software/2026/09/09/edge-ai-extensions/5295185"


def test_source_link_is_added_when_the_text_does_not_carry_it():
    out = broadcasters.ensure_mastodon_source_link(["Review capacity is the bottleneck."], SRC)
    assert out == ["Review capacity is the bottleneck." + P + SRC]


@pytest.mark.parametrize("text", [
    "Read it: " + SRC,
    "Read it: " + SRC.replace("https://", "http://"),
    "Read it: " + SRC + "?utm_source=rss&utm_medium=feed",
    "Read it: " + SRC + "/",
    "Read it (" + SRC + ").",
    "Read it: theregister.com/software/2026/09/09/edge-ai-extensions/5295185",
])
def test_a_link_already_in_the_text_is_not_duplicated(text):
    assert broadcasters.ensure_mastodon_source_link([text], SRC) == [text]


def test_arxiv_abs_and_pdf_forms_count_as_the_same_link():
    text = "The paper: https://arxiv.org/pdf/2609.06391v2"
    assert broadcasters.ensure_mastodon_source_link([text], "https://arxiv.org/abs/2609.06391") == [text]


def test_a_link_in_a_later_part_is_enough():
    parts = ["Part one.", "Part two, source: " + SRC]
    assert broadcasters.ensure_mastodon_source_link(parts, SRC) == parts


@pytest.mark.parametrize("source", [None, "", "   "])
def test_no_source_means_no_change(source):
    assert broadcasters.ensure_mastodon_source_link(["A mentor post."], source) == ["A mentor post."]


def test_a_link_that_would_overflow_is_skipped_and_logged(monkeypatch):
    warned = []
    monkeypatch.setattr(broadcasters.SafeLogger, "warn", lambda event, *a, **k: warned.append(event))
    root = "x" * 300
    long_url = "https://example.com/" + "a" * 250
    assert broadcasters.ensure_mastodon_source_link([root], long_url, max_length=500) == [root]
    assert warned == ["mastodon_source_link_too_long"]


def test_link_then_marker_then_tags_on_a_thread():
    """The link joins the root as its own paragraph, the marker ends the part's
    text, and the tags stay a hashtag-only final paragraph."""
    parts = broadcasters.ensure_mastodon_source_link(["Prose.", "Part two."], SRC)
    parts = broadcasters.number_mastodon_thread(parts)
    parts = broadcasters.apply_mastodon_tags(parts, ["#AI", "#Security"])
    assert parts == ["Prose." + P + SRC + " 1/2" + P + "#AI #Security", "Part two. 2/2"]


@pytest.mark.asyncio
async def test_post_to_mastodon_carries_the_source_link(monkeypatch):
    sent = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            pass

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None, idempotency_key=None):
            sent.append(status)
            return {"id": str(len(sent))}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example", ["Prose."], tags=["#AI"], source_url=SRC,
    )

    assert sent == ["Prose." + P + SRC + P + "#AI"]


@pytest.mark.asyncio
async def test_the_bluesky_text_is_not_given_the_link(monkeypatch):
    """Bluesky already shows the source as a card; its text stays as generated."""
    sent = []

    class FakeUpload:
        blob = None

    class FakePost:
        cid = "bafyreid"
        uri = "at://did:plc:test/app.bsky.feed.post/1"

    class DummyAsyncClient:
        async def upload_blob(self, data):
            return FakeUpload()

        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            sent.append(text)
            return FakePost()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    await broadcasters.post_to_bluesky(
        DummyAsyncClient(), ["Prose."],
        {"title": "T", "description": "", "image_data": None, "url": SRC},
    )

    assert sent == ["Prose."]
