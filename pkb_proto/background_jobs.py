"""Shared background executors for local Secretary jobs.

The regular serial executor keeps the existing ThreadPoolExecutor semantics for
jobs which are expected to finish before process exit.  The daemon variant is
reserved for long-running, recoverable background work which must never keep
the local Web server alive after shutdown.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, Callable


class SerialBackgroundExecutor:
    """One queued worker with the standard ThreadPoolExecutor lifecycle."""

    def __init__(self, name: str):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def close(self, *, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


_STOP = object()


class DaemonSerialBackgroundExecutor:
    """One serial daemon worker for recoverable long-running background jobs.

    Unlike ThreadPoolExecutor, the worker is a daemon thread, so a running job
    cannot keep the Python process alive after the Web server shuts down.
    Callers must therefore make in-progress state recoverable and use close()
    to cancel work which has not started yet.
    """

    def __init__(self, name: str):
        self._name = name
        self._queue: Queue = Queue()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._closed = False

    def _start_locked(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._worker,
            name=self._name,
            daemon=True,
        )
        self._thread.start()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot schedule new futures after shutdown")
            self._start_locked()
            self._queue.put((future, fn, args, kwargs))
        return future

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                future, fn, args, kwargs = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            finally:
                self._queue.task_done()

    def close(
        self,
        *,
        wait: bool = False,
        cancel_pending: bool = True,
    ) -> None:
        """Stop accepting work and optionally cancel queued jobs.

        wait=False is the intended shutdown mode.  A currently running job is
        not force-killed; because the worker is daemonized it cannot block
        interpreter exit.
        """
        with self._lock:
            if self._closed:
                thread = self._thread
            else:
                self._closed = True
                if cancel_pending:
                    while True:
                        try:
                            item = self._queue.get_nowait()
                        except Empty:
                            break
                        try:
                            if item is not _STOP:
                                future = item[0]
                                future.cancel()
                        finally:
                            self._queue.task_done()
                thread = self._thread
                if thread is not None:
                    self._queue.put(_STOP)
        if wait and thread is not None:
            thread.join()
