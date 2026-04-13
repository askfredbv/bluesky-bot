import traceback
from typing import Optional, Any

class SafeLogger:
    """Security-focused logger that prevents raw API response metadata from leaking in logs."""
    
    @staticmethod
    def info(msg: str):
        print(f"INFO: {msg}")

    @staticmethod
    def error(msg: str, exception: Optional[BaseException] = None):
        """Logs a sanitized error message and the exception type without raw data."""
        error_type = type(exception).__name__ if exception else "Unknown"
        print(f"ERROR: {msg} [Type: {error_type}]")
        
        # We check a 'DEBUG' environment variable if we wanted full traces,
        # but for production 'askfred' engine, we keep it clean.
        # if os.environ.get("DEBUG"): print(traceback.format_exc())

    @staticmethod
    def warn(msg: str):
        print(f"WARNING: {msg}")
