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
    post_jitter_min_seconds: int
    post_jitter_max_seconds: int
    max_api_retries: int
    max_generation_retries: int
    feed_request_connect_timeout_seconds: float
    feed_request_read_timeout_seconds: float
    feed_request_write_timeout_seconds: float
    feed_request_pool_timeout_seconds: float
    min_thread_posts: int
    max_thread_posts: int


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
            max_api_retries=_get_int(source, "MAX_API_RETRIES", config.MAX_API_RETRIES),
            max_generation_retries=_get_int(source, "MAX_GENERATION_RETRIES", config.MAX_GENERATION_RETRIES),
            feed_request_connect_timeout_seconds=_get_float(
                source,
                "FEED_REQUEST_CONNECT_TIMEOUT_SECONDS",
                config.FEED_REQUEST_CONNECT_TIMEOUT_SECONDS,
            ),
            feed_request_read_timeout_seconds=_get_float(
                source,
                "FEED_REQUEST_READ_TIMEOUT_SECONDS",
                config.FEED_REQUEST_READ_TIMEOUT_SECONDS,
            ),
            feed_request_write_timeout_seconds=_get_float(
                source,
                "FEED_REQUEST_WRITE_TIMEOUT_SECONDS",
                config.FEED_REQUEST_WRITE_TIMEOUT_SECONDS,
            ),
            feed_request_pool_timeout_seconds=_get_float(
                source,
                "FEED_REQUEST_POOL_TIMEOUT_SECONDS",
                config.FEED_REQUEST_POOL_TIMEOUT_SECONDS,
            ),
            min_thread_posts=_get_int(source, "MIN_THREAD_POSTS", config.MIN_THREAD_POSTS),
            max_thread_posts=_get_int(source, "MAX_THREAD_POSTS", config.MAX_THREAD_POSTS),
        )

        _validate_platform(platform)
        return cls(credentials=creds, platform=platform)


def _validate_platform(platform: PlatformSettings) -> None:
    errors = []

    if platform.post_jitter_min_seconds < 0:
        errors.append("POST_JITTER_MIN_SECONDS must be >= 0")
    if platform.post_jitter_max_seconds < platform.post_jitter_min_seconds:
        errors.append("POST_JITTER_MAX_SECONDS must be >= POST_JITTER_MIN_SECONDS")

    if platform.max_api_retries < 0:
        errors.append("MAX_API_RETRIES must be >= 0")
    if platform.max_generation_retries < 0:
        errors.append("MAX_GENERATION_RETRIES must be >= 0")

    for field_name in (
        "feed_request_connect_timeout_seconds",
        "feed_request_read_timeout_seconds",
        "feed_request_write_timeout_seconds",
        "feed_request_pool_timeout_seconds",
    ):
        if getattr(platform, field_name) <= 0:
            errors.append(f"{field_name.upper()} must be > 0")

    if platform.min_thread_posts < 1:
        errors.append("MIN_THREAD_POSTS must be >= 1")
    if platform.max_thread_posts < platform.min_thread_posts:
        errors.append("MAX_THREAD_POSTS must be >= MIN_THREAD_POSTS")

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


def _get_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SettingsValidationError(f"{key} must be a float") from exc
