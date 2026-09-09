import pytest

from main import load_settings_or_exit
from src.settings import Settings, SettingsValidationError


VALID_ENV = {
    "GEMINI_API_KEY": "g-key",
    "BLUESKY_USERNAME": "askfred.be",
    "BLUESKY_APP_PASSWORD": "bsky-pass",
}


def test_settings_from_env_rejects_jitter_window_inversion():
    env = {
        **VALID_ENV,
        "POST_JITTER_MIN_SECONDS": "30",
        "POST_JITTER_MAX_SECONDS": "10",
    }

    with pytest.raises(SettingsValidationError, match="POST_JITTER_MAX_SECONDS must be >= POST_JITTER_MIN_SECONDS"):
        Settings.from_env(env)


def test_settings_no_longer_pretends_to_wire_what_it_never_wired():
    """Retry budgets, feed timeouts and thread bounds were parsed AND validated
    here while every runtime caller imported the static constant from src.config
    instead -- MAX_API_RETRIES=5 passed validation and changed nothing.

    These two tests previously asserted that validation, which was the most
    convincing part of the illusion: a knob that rejects bad values looks wired.
    The fields are gone, so the env vars are now plainly ignored rather than
    ceremonially checked. Same call #85 made for RECENT_POSTS_LIMIT."""
    env = {
        **VALID_ENV,
        "MAX_API_RETRIES": "-1",                   # would once have raised
        "FEED_REQUEST_READ_TIMEOUT_SECONDS": "0",  # would once have raised
        "MIN_THREAD_POSTS": "4",                   # would once have raised
        "MAX_THREAD_POSTS": "2",                   # against MIN above
    }

    settings = Settings.from_env(env)  # no error: these are not settings any more

    for gone in ("max_api_retries", "max_generation_retries", "min_thread_posts",
                 "max_thread_posts", "feed_request_read_timeout_seconds"):
        assert not hasattr(settings.platform, gone), f"{gone} is dead plumbing"
    # The pair that genuinely reaches the runtime (main.py:105-108) stays.
    assert settings.platform.post_jitter_min_seconds >= 0
    assert settings.platform.post_jitter_max_seconds >= settings.platform.post_jitter_min_seconds


def test_settings_from_env_requires_core_credentials():
    env = {
        "BLUESKY_USERNAME": "askfred.be",
        "BLUESKY_APP_PASSWORD": "bsky-pass",
    }

    with pytest.raises(SettingsValidationError, match="Missing required environment variable: GEMINI_API_KEY"):
        Settings.from_env(env)


def test_load_settings_or_exit_surfaces_startup_failure_message(monkeypatch, capsys):
    def _raise_validation_error():
        raise SettingsValidationError("POST_JITTER_MAX_SECONDS must be >= POST_JITTER_MIN_SECONDS")

    monkeypatch.setattr("main.Settings.from_env", _raise_validation_error)

    with pytest.raises(SystemExit) as excinfo:
        load_settings_or_exit()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "Configuration error:" in captured.out
    assert "POST_JITTER_MAX_SECONDS must be >= POST_JITTER_MIN_SECONDS" in captured.out
