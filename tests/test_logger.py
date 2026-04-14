import io
import json
import logging

from src.logger import SafeLogger


def _reset_logger_state() -> None:
    SafeLogger._logger.handlers.clear()
    SafeLogger._is_configured = False
    SafeLogger._context = {"run_id": None, "platform": None, "mode": None}
    SafeLogger._redaction_filter = None


def _capture_stream() -> io.StringIO:
    stream = io.StringIO()
    handler = SafeLogger._logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(stream)
    return stream


def _read_records(stream: io.StringIO):
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_repeated_log_calls_redact_with_cached_secret(monkeypatch):
    _reset_logger_state()
    monkeypatch.setenv("MY_API_TOKEN", "super-secret-token-123")

    SafeLogger.configure()
    stream = _capture_stream()

    for i in range(30):
        SafeLogger.info("repeat", f"call {i}: super-secret-token-123")

    records = _read_records(stream)
    assert len(records) == 30
    assert all("super-secret-token-123" not in record["message"] for record in records)
    assert all("[REDACTED]" in record["message"] for record in records)


def test_redacts_secrets_in_message_and_extra_fields(monkeypatch):
    _reset_logger_state()
    monkeypatch.setenv("SERVICE_PASSWORD", "S3rv1ce-Password-987")

    SafeLogger.configure(run_id="run-1")
    stream = _capture_stream()

    SafeLogger.info(
        "redact_fields",
        "login failed with S3rv1ce-Password-987",
        detail="token=S3rv1ce-Password-987",
    )

    record = _read_records(stream)[0]
    assert record["message"] == "login failed with [REDACTED]"
    assert record["detail"] == "token=[REDACTED]"


def test_redacts_multiple_occurrences_in_single_string(monkeypatch):
    _reset_logger_state()
    monkeypatch.setenv("THIRD_PARTY_KEY", "abcDEF123!@#")

    SafeLogger.configure()
    stream = _capture_stream()

    SafeLogger.info("multi", "abcDEF123!@# :: abcDEF123!@# :: abcDEF123!@#")

    record = _read_records(stream)[0]
    assert record["message"] == "[REDACTED] :: [REDACTED] :: [REDACTED]"


def test_redaction_cache_is_deterministic_until_refresh(monkeypatch):
    _reset_logger_state()
    monkeypatch.setenv("MY_API_KEY", "alphaAlpha123")

    SafeLogger.configure()
    stream = _capture_stream()

    SafeLogger.info("before_change", "alphaAlpha123")

    monkeypatch.setenv("MY_API_KEY", "betaBeta123")
    SafeLogger.info("without_refresh", "betaBeta123")

    SafeLogger.refresh_redaction_cache()
    SafeLogger.info("after_refresh", "betaBeta123")

    records = _read_records(stream)
    assert records[0]["message"] == "[REDACTED]"
    assert records[1]["message"] == "betaBeta123"
    assert records[2]["message"] == "[REDACTED]"


def test_short_or_low_entropy_secrets_are_not_redacted(monkeypatch):
    _reset_logger_state()
    monkeypatch.setenv("SHORT_TOKEN", "abcd123")
    monkeypatch.setenv("LOW_ENTROPY_PASSWORD", "aaaaaaaaaaaaaaaa")

    SafeLogger.configure()
    stream = _capture_stream()

    SafeLogger.info("entropy", "short=abcd123 low=aaaaaaaaaaaaaaaa")

    record = _read_records(stream)[0]
    assert record["message"] == "short=abcd123 low=aaaaaaaaaaaaaaaa"


def test_json_record_contains_expected_core_fields():
    _reset_logger_state()
    SafeLogger.configure(run_id="run-xyz", platform="system", mode="mentor")
    stream = _capture_stream()

    SafeLogger.info("core_fields", "hello", attempt=2, extra_key="value")

    record = _read_records(stream)[0]
    assert record["timestamp"]
    assert record["level"] == "INFO"
    assert record["event"] == "core_fields"
    assert record["message"] == "hello"
    assert record["run_id"] == "run-xyz"
    assert record["platform"] == "system"
    assert record["mode"] == "mentor"
    assert record["attempt"] == 2
    assert record["extra_key"] == "value"
