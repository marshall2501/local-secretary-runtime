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

import random
import time
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


class AsyncRetryExhausted(RuntimeError):
    """A retry-enabled request finished without a usable response."""

    def __init__(self, cause: Exception, diagnostic: dict):
        super().__init__("retry_exhausted")
        self.cause = cause
        self.diagnostic = diagnostic


async def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> Any:
    """Send one cancellable JSON request with an overall hard deadline."""
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


def _retry_final_status(exc: Exception) -> str:
    if isinstance(exc, AsyncHTTPStatusError):
        return f"http_{exc.status_code}"
    if isinstance(exc, AsyncRequestTimeout):
        return "timeout"
    if isinstance(exc, AsyncTransportError):
        return "transport_error"
    return type(exc).__name__


async def request_json_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float,
    retry_enabled: bool,
    retry_http_codes: tuple[int, ...] | list[int],
    max_retries: int = 3,
    retry_budget_fraction: float = 0.5,
    client: httpx.AsyncClient | None = None,
) -> tuple[Any, dict]:
    """Send one logical request, optionally retrying transient failures.

    The member timeout remains the hard overall deadline. Once the first
    retryable failure occurs, retries get at most half of the configured member
    timeout, further bounded by the remaining hard deadline. Retries never
    create extra MAGI votes or turns.
    """
    timeout = float(timeout)
    if timeout <= 0:
        raise ValueError("timeout_must_be_positive")
    if max_retries < 0:
        raise ValueError("max_retries_must_be_nonnegative")
    if not 0 < retry_budget_fraction <= 1:
        raise ValueError("invalid_retry_budget_fraction")

    retry_codes = {int(code) for code in retry_http_codes}
    started = time.monotonic()
    retry_started: float | None = None
    retry_budget_seconds = timeout * retry_budget_fraction
    attempts = 0
    status_codes_seen: list[int] = []
    wait_seconds = 0.0

    def diagnostic(final_status: str) -> dict:
        return {
            "retry_enabled": bool(retry_enabled),
            "attempt_count": attempts,
            "retry_count": max(0, attempts - 1),
            "retry_http_codes_seen": status_codes_seen,
            "retry_wait_seconds": round(wait_seconds, 3),
            "retry_budget_seconds": round(retry_budget_seconds, 3),
            "final_status": final_status,
        }

    async def run(active: httpx.AsyncClient) -> tuple[Any, dict]:
        nonlocal attempts, retry_started, wait_seconds
        while True:
            attempts += 1
            now = time.monotonic()
            elapsed = now - started
            remaining_total = timeout - elapsed
            if remaining_total <= 0:
                exc = AsyncRequestTimeout("request_deadline_exceeded")
                raise AsyncRetryExhausted(exc, diagnostic("timeout"))

            attempt_timeout = remaining_total
            if retry_started is not None:
                remaining_retry_budget = (
                    retry_budget_seconds - (now - retry_started)
                )
                if remaining_retry_budget <= 0:
                    exc = AsyncRequestTimeout("retry_budget_exceeded")
                    raise AsyncRetryExhausted(
                        exc, diagnostic("retry_budget_exceeded")
                    )
                attempt_timeout = min(
                    attempt_timeout,
                    remaining_retry_budget,
                )

            try:
                result = await request_json(
                    method,
                    url,
                    headers=headers,
                    json_body=json_body,
                    timeout=attempt_timeout,
                    client=active,
                )
                return result, diagnostic("ok")
            except (AsyncHTTPStatusError, AsyncTransportError) as exc:
                if isinstance(exc, AsyncHTTPStatusError):
                    status_codes_seen.append(exc.status_code)
                    retryable = exc.status_code in retry_codes
                else:
                    retryable = True

                retries_used = attempts - 1
                if (
                    not retry_enabled
                    or not retryable
                    or retries_used >= max_retries
                ):
                    raise AsyncRetryExhausted(
                        exc, diagnostic(_retry_final_status(exc))
                    ) from exc

                now = time.monotonic()
                if retry_started is None:
                    retry_started = now
                retry_elapsed = now - retry_started
                remaining_retry_budget = retry_budget_seconds - retry_elapsed
                remaining_total = timeout - (now - started)
                if remaining_retry_budget <= 0 or remaining_total <= 0:
                    raise AsyncRetryExhausted(
                        exc, diagnostic(_retry_final_status(exc))
                    ) from exc

                backoff = (2 ** retries_used) + random.uniform(0.0, 0.25)
                delay = min(backoff, remaining_retry_budget, remaining_total)
                if delay <= 0:
                    raise AsyncRetryExhausted(
                        exc, diagnostic(_retry_final_status(exc))
                    ) from exc
                await anyio.sleep(delay)
                wait_seconds += delay
            except AsyncRequestTimeout as exc:
                raise AsyncRetryExhausted(
                    exc, diagnostic("timeout")
                ) from exc

    try:
        with anyio.fail_after(timeout):
            if client is not None:
                return await run(client)
            async with httpx.AsyncClient(timeout=None) as owned:
                return await run(owned)
    except AsyncRetryExhausted:
        raise
    except TimeoutError as exc:
        timeout_exc = AsyncRequestTimeout("request_deadline_exceeded")
        raise AsyncRetryExhausted(
            timeout_exc, diagnostic("timeout")
        ) from exc
