"""
jevguard.exceptions - Typed exception hierarchy for JevGuard runtime.
"""

from typing import Any, Optional


class JevGuardError(RuntimeError):
    """Base exception for all JevGuard runtime errors."""
    pass


class JevGuardConfigError(JevGuardError):
    """Raised when configuration, model or credentials are missing/invalid."""
    pass


class JevGuardNetworkError(JevGuardError):
    """Base exception for network and transport failures."""
    pass


class JevGuardTimeoutError(JevGuardNetworkError):
    """Raised when a network request to TypeSafe AI times out."""

    def __init__(self, message: str, timeout: Optional[float] = None):
        super().__init__(message)
        self.timeout = timeout


class JevGuardHTTPError(JevGuardError):
    """Raised when TypeSafe AI returns an HTTP error status code."""

    def __init__(
        self,
        status_code: int,
        message: str,
        payload: Any = None,
        retry_after: Optional[float] = None
    ):
        super().__init__(f"TypeSafe AI HTTP Error {status_code}: {message}")
        self.status_code = status_code
        self.payload = payload
        self.retry_after = retry_after


class JevGuardAuthenticationError(JevGuardHTTPError):
    """Raised on HTTP 401 or 403 authorization failures."""
    pass


class JevGuardRateLimitError(JevGuardHTTPError):
    """Raised on HTTP 429 Too Many Requests."""
    pass


class JevGuardServerError(JevGuardHTTPError):
    """Raised on HTTP 500, 502, 503, or 504 server errors."""
    pass
