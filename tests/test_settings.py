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


def test_settings_from_env_rejects_thread_length_inversion():
    env = {
        **VALID_ENV,
        "MIN_THREAD_POSTS": "4",
        "MAX_THREAD_POSTS": "2",
    }

    with pytest.raises(SettingsValidationError, match="MAX_THREAD_POSTS must be >= MIN_THREAD_POSTS"):
        Settings.from_env(env)


def test_settings_from_env_rejects_negative_retries_and_nonpositive_timeouts():
    env = {
        **VALID_ENV,
        "MAX_API_RETRIES": "-1",
        "FEED_REQUEST_READ_TIMEOUT_SECONDS": "0",
    }

    with pytest.raises(SettingsValidationError) as excinfo:
        Settings.from_env(env)

    message = str(excinfo.value)
    assert "MAX_API_RETRIES must be >= 0" in message
    assert "FEED_REQUEST_READ_TIMEOUT_SECONDS must be > 0" in message


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
