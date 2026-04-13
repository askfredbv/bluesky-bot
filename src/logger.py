import os
import traceback
from typing import Optional, Any

class SafeLogger:
    """Security-focused logger that prevents raw API response metadata from leaking in logs."""
    
    @classmethod
    def _sanitize(cls, msg: str) -> str:
        if not isinstance(msg, str):
            msg = str(msg)
        for key, value in os.environ.items():
            if ("KEY" in key or "TOKEN" in key or "PASSWORD" in key) and value and len(value) > 4:
                msg = msg.replace(value, "[REDACTED]")
        return msg

    @classmethod
    def info(cls, msg: str):
        print(f"INFO: {cls._sanitize(msg)}")

    @classmethod
    def error(cls, msg: str, exception: Optional[BaseException] = None):
        """Logs a sanitized error message and the exception type without raw data."""
        error_type = type(exception).__name__ if exception else "Unknown"
        safe_msg = cls._sanitize(msg)
        safe_exc = cls._sanitize(str(exception)) if exception else ""
        print(f"ERROR: {safe_msg} [Type: {error_type}] {safe_exc}")

    @classmethod
    def warn(cls, msg: str):
        print(f"WARNING: {cls._sanitize(msg)}")
