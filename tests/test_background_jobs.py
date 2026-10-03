from __future__ import annotations

import threading
import unittest

from pkb_proto.background_jobs import DaemonSerialBackgroundExecutor


class DaemonSerialBackgroundExecutorTests(unittest.TestCase):
    def test_worker_is_daemon_and_runs_serially(self):
        executor = DaemonSerialBackgroundExecutor("daemon-serial-test")
        seen = []
        try:
            first = executor.submit(lambda: seen.append("first"))
            second = executor.submit(lambda: seen.append("second"))
            first.result(timeout=1)
            second.result(timeout=1)
            self.assertEqual(seen, ["first", "second"])
            self.assertIsNotNone(executor._thread)
            self.assertTrue(executor._thread.daemon)
        finally:
            executor.close(wait=True)

    def test_close_cancels_pending_without_waiting_for_running_job(self):
        executor = DaemonSerialBackgroundExecutor("daemon-cancel-test")
        started = threading.Event()
        release = threading.Event()

        def running():
            started.set()
            release.wait(1)
            return "done"

        first = executor.submit(running)
        self.assertTrue(started.wait(1))
        second = executor.submit(lambda: "should-not-run")
        executor.close(wait=False, cancel_pending=True)

        self.assertTrue(second.cancelled())
        self.assertFalse(first.done())
        release.set()
        self.assertEqual(first.result(timeout=1), "done")

    def test_submit_after_close_is_rejected(self):
        executor = DaemonSerialBackgroundExecutor("daemon-closed-test")
        executor.close(wait=False)
        with self.assertRaises(RuntimeError):
            executor.submit(lambda: None)


if __name__ == "__main__":
    unittest.main()
