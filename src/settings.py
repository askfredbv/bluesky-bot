from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional

from src import config


class SettingsValidationError(ValueError):
    """Raised when environment configuration is invalid."""


@dataclass(frozen=True)
class CredentialsSettings:
    gemini_api_key: str
    bluesky_username: str
    bluesky_password: str
    mastodon_access_token: Optional[str]
    mastodon_api_base_url: str


@dataclass(frozen=True)
class PlatformSettings:
    """Environment-overridable platform knobs.

    Only what is actually consumed lives here. Retry budgets, feed timeouts and
    thread bounds used to be parsed and validated in this dataclass while every
    runtime caller imported the static constant from src.config instead, so
    setting MAX_API_RETRIES=5 passed validation and changed nothing. Same class
    of defect as the constants #85 removed for being "a lie about a tunable" --
    a knob that reports success and does nothing is worse than no knob.
    """

    post_jitter_min_seconds: int
    post_jitter_max_seconds: int


@dataclass(frozen=True)
class Settings:
    credentials: CredentialsSettings
    platform: PlatformSettings

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        source = env or os.environ

        creds = CredentialsSettings(
            gemini_api_key=_get_required_str(source, "GEMINI_API_KEY"),
            bluesky_username=_get_optional_str(source, "BLUESKY_USERNAME", "askfred.be"),
            bluesky_password=_get_required_str(
                source,
                "BLUESKY_APP_PASSWORD",
                fallback_key="BLUESKY_PASSWORD",
            ),
            mastodon_access_token=_get_nullable_str(source, "MASTODON_ACCESS_TOKEN"),
            mastodon_api_base_url=_get_optional_str(
                source,
                "MASTODON_API_BASE_URL",
                "https://mastodon.social",
            ),
        )

        platform = PlatformSettings(
            post_jitter_min_seconds=_get_int(source, "POST_JITTER_MIN_SECONDS", config.POST_JITTER_MIN_SECONDS),
            post_jitter_max_seconds=_get_int(source, "POST_JITTER_MAX_SECONDS", config.POST_JITTER_MAX_SECONDS),
        )

        _validate_platform(platform)
        return cls(credentials=creds, platform=platform)


def _validate_platform(platform: PlatformSettings) -> None:
    errors = []

    if platform.post_jitter_min_seconds < 0:
        errors.append("POST_JITTER_MIN_SECONDS must be >= 0")
    if platform.post_jitter_max_seconds < platform.post_jitter_min_seconds:
        errors.append("POST_JITTER_MAX_SECONDS must be >= POST_JITTER_MIN_SECONDS")

    if errors:
        raise SettingsValidationError("; ".join(errors))


def _get_required_str(env: Mapping[str, str], key: str, fallback_key: Optional[str] = None) -> str:
    raw = env.get(key)
    if raw is None and fallback_key:
        raw = env.get(fallback_key)
    if raw is None or not raw.strip():
        alt = f" (or {fallback_key})" if fallback_key else ""
        raise SettingsValidationError(f"Missing required environment variable: {key}{alt}")
    return raw.strip()


def _get_optional_str(env: Mapping[str, str], key: str, default: str) -> str:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


def _get_nullable_str(env: Mapping[str, str], key: str) -> Optional[str]:
    raw = env.get(key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _get_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsValidationError(f"{key} must be an integer") from exc


