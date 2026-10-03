"""MAGI-owned async transport contract and transport-neutral errors."""
from __future__ import annotations


class AsyncRequestTimeout(TimeoutError):
    """The caller-owned overall deadline expired."""


class AsyncHTTPStatusError(RuntimeError):
    """HTTP response completed with a non-success status."""

    def __init__(
        self,
        status_code: int,
        *,
        provider_status: str | None = None,
        provider_message: str | None = None,
    ):
        super().__init__(f"http_status_{status_code}")
        self.status_code = int(status_code)
        self.provider_status = provider_status
        self.provider_message = provider_message


class AsyncTransportError(RuntimeError):
    """Transport failed before a usable response was received."""


class AsyncRetryExhausted(RuntimeError):
    """A retry-enabled request finished without a usable response."""

    def __init__(self, cause: Exception, diagnostic: dict):
        super().__init__("retry_exhausted")
        self.cause = cause
        self.diagnostic = diagnostic
