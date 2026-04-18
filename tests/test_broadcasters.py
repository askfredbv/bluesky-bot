import asyncio
import pytest

from atproto import models
from src.config import MAX_POST_LENGTH_BSKY, MAX_POST_LENGTH_MASTODON
from src import broadcasters


@pytest.mark.asyncio
async def test_post_to_bluesky_splits_overlong_content(monkeypatch):
    sent_payloads = []
    warnings = []

    class DummyPost:
        def __init__(self, idx):
            self.cid = f"cid-{idx}"
            self.uri = f"at://post/{idx}"

    class DummyAsyncClient:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            sent_payloads.append(
                {"text": text, "embed": embed, "reply_to": reply_to, "facets": facets}
            )
            return DummyPost(len(sent_payloads))

    async def no_sleep(_):
        return None

    def capture_warn(event, message="", **fields):
        warnings.append((event, message, fields))

    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(broadcasters.SafeLogger, "warn", capture_warn)

    overlong = "x" * (MAX_POST_LENGTH_BSKY + 25)
    dummy_client = DummyAsyncClient()
    await broadcasters.post_to_bluesky(dummy_client, [overlong])

    assert len(sent_payloads) == 2
    assert all(len(payload["text"]) <= MAX_POST_LENGTH_BSKY for payload in sent_payloads)
    assert warnings


@pytest.mark.asyncio
async def test_post_to_mastodon_splits_overlong_content(monkeypatch):
    posted_statuses = []

    class DummyMastodon:
        def __init__(self, access_token, api_base_url):
            self.access_token = access_token
            self.api_base_url = api_base_url

        def instance(self):
            return {
                "configuration": {
                    "statuses": {
                        "max_characters": MAX_POST_LENGTH_MASTODON,
                        "characters_reserved_per_url": 23,
                    }
                }
            }

        def status_post(self, status, in_reply_to_id, visibility):
            posted_statuses.append(
                {
                    "status": status,
                    "in_reply_to_id": in_reply_to_id,
                    "visibility": visibility,
                }
            )
            return {"id": len(posted_statuses)}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)

    overlong = "y" * (MAX_POST_LENGTH_MASTODON + 40)
    await broadcasters.post_to_mastodon("token", "https://mastodon.example", [overlong])

    assert len(posted_statuses) == 2
    assert all(
        len(payload["status"]) <= MAX_POST_LENGTH_MASTODON for payload in posted_statuses
    )


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
