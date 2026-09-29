"""Shared background executor for local Secretary jobs.

Both the PKB developer Workbench and Secretary Core shadow jobs use this
single-worker abstraction so browser lifecycle does not own long-running model
calls. Domain-specific state stays in each caller's store/DB.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class SerialBackgroundExecutor:
    """One queued worker with an explicit lifecycle."""

    def __init__(self, name: str):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def close(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
