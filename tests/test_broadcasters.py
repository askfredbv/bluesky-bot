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

        def status_post(self, status, in_reply_to_id, visibility):
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

        def status_post(self, status, in_reply_to_id, visibility):
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
    out, fits = broadcasters._compress_image_to_fit(data, 1024)
    assert out is data
    assert fits is True


def test_compress_reencodes_oversized_image_under_the_real_gate():
    png = _make_oversized_png(600)
    assert len(png) > broadcasters._BLUESKY_IMAGE_MAX_BYTES  # over the real gate
    out, fits = broadcasters._compress_image_to_fit(png, broadcasters._BLUESKY_IMAGE_MAX_BYTES)
    assert fits is True
    assert len(out) <= broadcasters._BLUESKY_IMAGE_MAX_BYTES
    assert out is not png  # actually re-encoded, not passed through


def test_compress_returns_false_when_no_budget_can_fit():
    png = _make_oversized_png(200)
    out, fits = broadcasters._compress_image_to_fit(png, 10)
    assert fits is False
    assert out is png  # nothing usable produced; caller will skip the attach


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

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None):
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

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None):
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

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None):
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

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None):
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

