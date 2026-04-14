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
        async def login(self, username, password):
            return None

        async def send_post(self, text, embed=None, reply_to=None):
            sent_payloads.append(
                {"text": text, "embed": embed, "reply_to": reply_to}
            )
            return DummyPost(len(sent_payloads))

    async def no_sleep(_):
        return None

    monkeypatch.setattr(broadcasters, "AsyncClient", DummyAsyncClient)
    monkeypatch.setattr(broadcasters.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(broadcasters.SafeLogger, "warn", warnings.append)

    overlong = "x" * (MAX_POST_LENGTH_BSKY + 25)
    await broadcasters.post_to_bluesky("user", "pass", [overlong])

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

        def status_post(self, status, in_reply_to_id, visibility):
            posted_statuses.append(
                {
                    "status": status,
                    "in_reply_to_id": in_reply_to_id,
                    "visibility": visibility,
                }
            )
            return {"id": len(posted_statuses)}

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.time, "sleep", lambda _: None)

    overlong = "y" * (MAX_POST_LENGTH_MASTODON + 40)
    await broadcasters.post_to_mastodon("token", "https://mastodon.example", [overlong])

    assert len(posted_statuses) == 2
    assert all(
        len(payload["status"]) <= MAX_POST_LENGTH_MASTODON for payload in posted_statuses
    )


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
