"""Behaviour the 2026-09-10 mutation run found unpinned (repo-quality tip 5).

Each test here was written against a specific surviving mutant: a small
deliberate change to src/broadcasters.py that the whole suite still passed.
The mutant each one kills is named in its docstring.
"""
import asyncio
import io
import threading

import pytest
from atproto import models
from PIL import Image

from src import broadcasters
from src.config import MAX_POST_LENGTH_MASTODON
from src.metrics import BroadcastResult


def _post(n):
    class FakePost:
        cid = f"cid-{n}"
        uri = f"at://post/{n}"
    return FakePost()


async def _no_sleep(_seconds):
    return None


@pytest.mark.asyncio
async def test_bluesky_post_carries_its_facets(monkeypatch):
    """Kills `build_facets(post_text) or None` -> `and None`. Without facets,
    every URL and hashtag in a Bluesky post is plain, unclickable text."""
    sent_facets = []

    class Client:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            sent_facets.append(facets)
            return _post(len(sent_facets))

    monkeypatch.setattr(broadcasters.asyncio, "sleep", _no_sleep)
    await broadcasters.post_to_bluesky(Client(), ["Read https://example.com/a for the detail"])

    assert sent_facets[0], "the post went out without facets"


@pytest.mark.asyncio
async def test_link_card_thumbnail_is_uploaded_and_attached(monkeypatch):
    """Kills negating `if link_meta.get('image_data')`. The publisher's
    thumbnail must be uploaded and set on the card, and nothing is uploaded
    when there is no thumbnail."""
    fake_blob = models.blob_ref.BlobRef(ref={"link": "bafkreiaa"}, mime_type="image/png", size=3)
    uploaded, embeds = [], []

    class Upload:
        blob = fake_blob

    class Client:
        async def upload_blob(self, data):
            uploaded.append(data)
            return Upload()

        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            embeds.append(embed)
            return _post(len(embeds))

    monkeypatch.setattr(broadcasters.asyncio, "sleep", _no_sleep)
    meta = {"title": "T", "description": "D", "url": "https://example.com/a"}

    await broadcasters.post_to_bluesky(Client(), ["With a thumbnail"], link_meta={**meta, "image_data": b"thumb"})
    assert uploaded == [b"thumb"]
    assert embeds[0].external.thumb == fake_blob

    await broadcasters.post_to_bluesky(Client(), ["Without one"], link_meta=meta)
    assert uploaded == [b"thumb"], "nothing to upload, so nothing may be uploaded"
    assert embeds[1].external.thumb is None


def test_a_different_url_in_the_text_does_not_count_as_the_source_link():
    """Kills `and` -> `or` in _text_mentions_url. With `or`, ANY link in the
    text counted as the source, so the real source never reached Mastodon."""
    out = broadcasters.ensure_mastodon_source_link(
        ["Compare this with https://other.example/post"], "https://example.com/a"
    )
    assert "https://example.com/a" in out[0]


@pytest.mark.asyncio
async def test_bluesky_cancellation_during_a_send_propagates_and_is_not_retried():
    """Kills `raise` -> `pass` in both CancelledError handlers of post_to_bluesky.
    A swallowed cancellation either made the retry loop send the post again, or
    let a cancelled broadcast return as if it had finished."""
    started = asyncio.Event()
    calls = []

    class Client:
        async def send_post(self, text, embed=None, reply_to=None, facets=None):
            calls.append(text)
            started.set()
            await asyncio.Event().wait()  # hangs until cancelled

    task = asyncio.create_task(broadcasters.post_to_bluesky(Client(), ["only post"]))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)

    assert calls == ["only post"]


@pytest.mark.asyncio
async def test_mastodon_cancellation_during_a_send_propagates_and_is_not_retried(monkeypatch):
    """Kills `raise` -> `pass` in post_to_mastodon's retry loop. A swallowed
    cancellation sent the same status again instead of stopping."""
    release = threading.Event()
    calls = []

    class DummyMastodon:
        def __init__(self, *a, **kw):
            pass

        def status_post(self, status, in_reply_to_id, visibility, media_ids=None, idempotency_key=None):
            calls.append(status)
            release.wait(timeout=5)  # the worker thread cannot be cancelled; free it below
            return {"id": len(calls)}

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    task = asyncio.create_task(
        broadcasters.post_to_mastodon("token", "https://mastodon.example", ["only post"])
    )
    try:
        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert calls, "status_post never started"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)
    finally:
        release.set()

    assert calls == ["only post"]


@pytest.mark.asyncio
async def test_mastodon_invariant_skip_returns_an_empty_broadcast_result(monkeypatch):
    """Kills `return BroadcastResult(...)` -> `return None` on the length
    invariant skip. Callers read .sent_uris off whatever comes back."""

    class DummyMastodon:
        def __init__(self, *a, **kw):
            raise AssertionError("must not connect when the invariant fails")

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    result = await broadcasters.post_to_mastodon(
        "token", "https://mastodon.example", ["y" * (MAX_POST_LENGTH_MASTODON + 1)]
    )

    assert isinstance(result, BroadcastResult)
    assert result.sent_uris == []
    assert result.client is None


def _image_bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.parametrize(
    "fmt, mime",
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("GIF", "image/gif"), ("WEBP", "image/webp")],
)
def test_image_mime_is_detected_from_the_bytes(fmt, mime):
    """Kills the mutants on _detect_image_mime, whose result no test asserted."""
    assert broadcasters._detect_image_mime(_image_bytes(fmt)) == mime


def test_undecodable_image_bytes_fall_back_to_png():
    assert broadcasters._detect_image_mime(b"not an image") == "image/png"
