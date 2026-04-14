import json
import logging
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Pattern


class _SecretRedactionFilter(logging.Filter):
    """Redacts environment-based secrets from all string log content."""

    _MIN_SECRET_LENGTH = 8
    _MIN_SECRET_ENTROPY = 2.5

    def __init__(self) -> None:
        super().__init__()
        self._secret_patterns: List[Pattern[str]] = []
        self.refresh_secrets()

    @classmethod
    def _has_min_entropy(cls, value: str) -> bool:
        if len(value) < cls._MIN_SECRET_LENGTH:
            return False

        counts = Counter(value)
        length = len(value)
        entropy = -sum((count / length) * math.log2(count / length) for count in counts.values())
        return entropy >= cls._MIN_SECRET_ENTROPY

    @classmethod
    def _is_redactable_secret(cls, key: str, secret: str) -> bool:
        if not secret:
            return False
        if not ("KEY" in key or "TOKEN" in key or "PASSWORD" in key):
            return False
        return cls._has_min_entropy(secret)

    def refresh_secrets(self) -> None:
        patterns: List[Pattern[str]] = []
        seen = set()
        for key, secret in os.environ.items():
            if not self._is_redactable_secret(key, secret):
                continue
            if secret in seen:
                continue
            seen.add(secret)
            patterns.append(re.compile(re.escape(secret)))
        self._secret_patterns = patterns

    def _sanitize(self, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        if not self._secret_patterns:
            return value
        for pattern in self._secret_patterns:
            value = pattern.sub("[REDACTED]", value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._sanitize(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._sanitize(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: self._sanitize(v) for k, v in record.args.items()}

        for attr, value in list(record.__dict__.items()):
            if isinstance(value, str):
                record.__dict__[attr] = self._sanitize(value)
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", "log_event"),
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "platform": getattr(record, "platform", None),
            "mode": getattr(record, "mode", None),
            "attempt": getattr(record, "attempt", None),
            "error_type": getattr(record, "error_type", None),
        }
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in payload or key in {
                "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "created", "msecs", "relativeCreated", "thread", "threadName",
                "processName", "process", "message", "asctime",
            }:
                continue
            payload[key] = value
        return json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False)


class SafeLogger:
    """Structured, security-focused logger with secret redaction."""

    _logger = logging.getLogger("askfred")
    _is_configured = False
    _context: Dict[str, Any] = {"run_id": None, "platform": None, "mode": None}
    _redaction_filter: Optional[_SecretRedactionFilter] = None

    @classmethod
    def _configure_if_needed(cls) -> None:
        if cls._is_configured:
            return
        cls._logger.setLevel(logging.INFO)
        cls._logger.propagate = False
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        cls._redaction_filter = _SecretRedactionFilter()
        handler.addFilter(cls._redaction_filter)
        cls._logger.handlers.clear()
        cls._logger.addHandler(handler)
        cls._is_configured = True

    @classmethod
    def refresh_redaction_cache(cls) -> None:
        cls._configure_if_needed()
        if cls._redaction_filter is not None:
            cls._redaction_filter.refresh_secrets()

    @classmethod
    def configure(cls, *, run_id: Optional[str] = None, platform: Optional[str] = None, mode: Optional[str] = None) -> None:
        cls._configure_if_needed()
        if run_id is not None:
            cls._context["run_id"] = run_id
        if platform is not None:
            cls._context["platform"] = platform
        if mode is not None:
            cls._context["mode"] = mode

    @classmethod
    def _emit(
        cls,
        level: int,
        event: str,
        message: str = "",
        *,
        exception: Optional[BaseException] = None,
        **fields: Any
    ) -> None:
        cls._configure_if_needed()
        payload = {**cls._context, **fields, "event": event}
        if exception is not None:
            payload["error_type"] = type(exception).__name__
            if message:
                message = f"{message}: {exception}"
            else:
                message = str(exception)
        cls._logger.log(level, message, extra=payload)

    @classmethod
    def info(cls, event: str, message: str = "", **fields: Any) -> None:
        cls._emit(logging.INFO, event, message, **fields)

    @classmethod
    def warn(cls, event: str, message: str = "", **fields: Any) -> None:
        cls._emit(logging.WARNING, event, message, **fields)

    @classmethod
    def error(cls, event: str, message: str = "", exception: Optional[BaseException] = None, **fields: Any) -> None:
        cls._emit(logging.ERROR, event, message, exception=exception, **fields)
