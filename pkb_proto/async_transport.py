"""Shared cancellable async HTTP transport for Local Secretary runtime.

Design rule:
- network I/O is async by default;
- each request has a hard overall deadline;
- cancellation propagates to the caller;
- blocking-only legacy code must be isolated outside the event loop.

The concrete HTTP client is replaceable. Callers depend on this small contract,
not on provider-specific client libraries.
"""
from __future__ import annotations

from typing import Any

import anyio
import httpx


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
    """HTTP transport failed before a usable response was received."""


async def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> Any:
    """Send one cancellable JSON request with an overall hard deadline.

    The timeout covers connect, upload, server wait, response download and JSON
    decoding. Cancellation from the parent task is deliberately not converted
    into an ordinary provider error.
    """
    if timeout <= 0:
        raise ValueError("timeout_must_be_positive")

    async def _send(active: httpx.AsyncClient) -> Any:
        response = await active.request(
            method,
            url,
            headers=headers,
            json=json_body,
        )
        if response.status_code < 200 or response.status_code >= 300:
            provider_status = None
            provider_message = None
            try:
                error_body = response.json()
                error = error_body.get("error") if isinstance(error_body, dict) else None
                if isinstance(error, dict):
                    status = error.get("status")
                    message = error.get("message")
                    provider_status = (
                        str(status).strip()[:80]
                        if status is not None and str(status).strip()
                        else None
                    )
                    provider_message = (
                        str(message).strip()[:400]
                        if message is not None and str(message).strip()
                        else None
                    )
            except ValueError:
                pass
            raise AsyncHTTPStatusError(
                response.status_code,
                provider_status=provider_status,
                provider_message=provider_message,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise AsyncTransportError("invalid_json_response") from exc

    try:
        with anyio.fail_after(float(timeout)):
            if client is not None:
                return await _send(client)
            async with httpx.AsyncClient(timeout=None) as owned:
                return await _send(owned)
    except TimeoutError as exc:
        raise AsyncRequestTimeout("request_deadline_exceeded") from exc
    except AsyncHTTPStatusError:
        raise
    except httpx.HTTPError as exc:
        raise AsyncTransportError(type(exc).__name__) from exc
