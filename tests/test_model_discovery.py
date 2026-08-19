"""Tests for Gemini model self-discovery (filter_available_models in agents.py)."""

import pytest
from src.agents import filter_available_models
from src import agents


class _FakeModel:
    def __init__(self, name):
        self.name = f"models/{name}"


@pytest.mark.asyncio
async def test_filter_removes_unavailable_model(monkeypatch):
    """A model absent from the API list is removed from the priority."""
    priority = ["gemini-2.5-flash", "gemini-2.0-flash", "gemma-3-27b-it"]

    class FakeClient:
        class models:
            @staticmethod
            def list():
                # gemini-2.0-flash not listed
                return [_FakeModel("gemini-2.5-flash"), _FakeModel("gemma-3-27b-it")]

    monkeypatch.setattr(agents, "_CLIENT_CACHE", {"testkey": FakeClient()})
    result = await filter_available_models("testkey", priority)

    assert "gemini-2.0-flash" not in result
    assert result == ["gemini-2.5-flash", "gemma-3-27b-it"]


@pytest.mark.asyncio
async def test_filter_returns_original_on_discovery_failure(monkeypatch):
    """If the API call raises, the original priority list is returned unchanged."""
    priority = ["gemini-2.5-flash", "gemini-2.0-flash"]

    class BrokenClient:
        class models:
            @staticmethod
            def list():
                raise RuntimeError("API unreachable")

    monkeypatch.setattr(agents, "_CLIENT_CACHE", {"testkey": BrokenClient()})
    result = await filter_available_models("testkey", priority)

    assert result == priority


@pytest.mark.asyncio
async def test_filter_returns_original_when_all_available(monkeypatch):
    """When all models are available the list is returned unchanged."""
    priority = ["gemini-2.5-flash", "gemini-2.0-flash"]

    class FullClient:
        class models:
            @staticmethod
            def list():
                return [_FakeModel("gemini-2.5-flash"), _FakeModel("gemini-2.0-flash"),
                        _FakeModel("gemma-3-27b-it")]

    monkeypatch.setattr(agents, "_CLIENT_CACHE", {"testkey": FullClient()})
    result = await filter_available_models("testkey", priority)

    assert result == priority


@pytest.mark.asyncio
async def test_filter_never_returns_empty_list(monkeypatch):
    """Even if no priorities match, the original list is returned as a safety net."""
    priority = ["gemini-2.5-flash"]

    class EmptyClient:
        class models:
            @staticmethod
            def list():
                return []  # no models at all

    monkeypatch.setattr(agents, "_CLIENT_CACHE", {"testkey": EmptyClient()})
    result = await filter_available_models("testkey", priority)

    assert result == priority


def test_upgrade_candidates_excludes_current_chain_and_older(monkeypatch, capsys):
    """_print_upgrade_candidates flags ONLY models newer than the current
    primary — never the primary itself, its in-chain fallbacks, older siblings,
    or Flash-Lite. Guards the probe/report path Codex flagged on PR #69."""
    from scripts import discover_models as dm

    monkeypatch.setattr(
        dm, "GEMINI_MODEL_PRIORITY",
        ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-2.5-pro"],
    )

    available = {
        "gemini-3.7-flash",       # current primary — not an upgrade
        "gemini-3.5-flash",       # in-chain fallback — not an upgrade
        "gemini-3.6-flash",       # older than the primary — not an upgrade
        "gemini-3.5-flash-lite",  # lower tier — never an upgrade
        "gemini-2.5-flash",       # older generation — not an upgrade
        "gemini-4.0-flash",       # genuinely newer — IS an upgrade
    }
    dm._print_upgrade_candidates(available)
    out = capsys.readouterr().out

    assert "CANDIDATE: gemini-4.0-flash" in out
    for not_a_candidate in (
        "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.6-flash",
        "gemini-3.5-flash-lite", "gemini-2.5-flash",
    ):
        assert f"CANDIDATE: {not_a_candidate}" not in out
