import pytest

from src import utils


@pytest.mark.asyncio
async def test_get_link_metadata_uses_explicit_timeouts(monkeypatch):
    captured = {"client_timeout": None, "redirect_timeouts": []}

    class _FakeResponse:
        text = "<html><head><title>ok</title></head></html>"

        def raise_for_status(self):
            return None

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_async_client(*args, **kwargs):
        captured["client_timeout"] = kwargs.get("timeout")
        return _FakeClient()

    async def fake_safe_redirects(client, url, **kwargs):
        captured["redirect_timeouts"].append(kwargs.get("timeout"))
        return _FakeResponse()

    monkeypatch.setattr(utils, "is_safe_public_url", lambda _: True)
    monkeypatch.setattr(utils.httpx, "AsyncClient", fake_async_client)
    monkeypatch.setattr(utils, "get_with_safe_redirects", fake_safe_redirects)

    meta = await utils.get_link_metadata("https://example.com/article")

    assert meta["title"] == "ok"
    assert captured["client_timeout"] == 10.0
    assert captured["redirect_timeouts"] == [10.0]
