import asyncio
import pytest

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
        async def send_post(self, text, embed=None, reply_to=None):
            sent_payloads.append(
                {"text": text, "embed": embed, "reply_to": reply_to}
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
