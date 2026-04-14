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

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.time, "sleep", lambda _: None)

    overlong = "y" * (MAX_POST_LENGTH_MASTODON + 40)
    await broadcasters.post_to_mastodon("token", "https://mastodon.example", [overlong])

    assert len(posted_statuses) == 2
    assert all(
        len(payload["status"]) <= MAX_POST_LENGTH_MASTODON for payload in posted_statuses
    )


@pytest.mark.asyncio
async def test_post_to_mastodon_does_not_split_long_url_when_weighted_length_fits(monkeypatch):
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
            posted_statuses.append(status)
            return {"id": len(posted_statuses)}

    monkeypatch.setattr(broadcasters, "Mastodon", DummyMastodon)
    monkeypatch.setattr(broadcasters.time, "sleep", lambda _: None)

    long_url = "https://example.com/" + ("a" * (MAX_POST_LENGTH_MASTODON + 200))
    await broadcasters.post_to_mastodon("token", "https://mastodon.example", [long_url])

    assert len(posted_statuses) == 1
    assert posted_statuses[0] == long_url
