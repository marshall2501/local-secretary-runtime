from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
import unittest

from scripts.db.run_production_runtime_rehearsal import _run_steps, run_rehearsal


class ProductionRuntimeRehearsalRunnerTests(unittest.TestCase):
    def _paths(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        admin = root / "admin.secret"
        admin.write_text("a" * 48, encoding="utf-8")
        runtime = root / "runtime.secret"
        return temp, admin, runtime

    def test_success_orders_provision_before_verify_and_removes_secret(self):
        temp, admin, runtime = self._paths()
        calls = []

        def runner(command, *, check, cwd):
            self.assertTrue(check)
            calls.append(Path(command[1]).name)
            secret_index = command.index("--runtime-secret-file") + 1
            self.assertEqual(Path(command[secret_index]), runtime)
            if Path(command[1]).name == "provision_daily_runtime.py":
                runtime.write_text("b" * 48, encoding="utf-8")
            else:
                self.assertTrue(runtime.is_file())
            return subprocess.CompletedProcess(command, 0)

        try:
            _run_steps(
                target_port=55432,
                live_port=5432,
                admin_secret_file=admin,
                runtime_secret_file=runtime,
                settings_source_port=5432,
                runner=runner,
            )
            self.assertEqual(
                calls,
                ["provision_daily_runtime.py", "verify_production_runtime.py"],
            )
            self.assertFalse(runtime.exists())
        finally:
            temp.cleanup()

    def test_provision_failure_stops_before_verify_and_removes_secret(self):
        temp, admin, runtime = self._paths()
        calls = []

        def runner(command, *, check, cwd):
            calls.append(Path(command[1]).name)
            runtime.write_text("c" * 48, encoding="utf-8")
            raise subprocess.CalledProcessError(9, command)

        try:
            with self.assertRaises(subprocess.CalledProcessError):
                _run_steps(
                    target_port=55432,
                    live_port=5432,
                    admin_secret_file=admin,
                    runtime_secret_file=runtime,
                    settings_source_port=None,
                    runner=runner,
                )
            self.assertEqual(calls, ["provision_daily_runtime.py"])
            self.assertFalse(runtime.exists())
        finally:
            temp.cleanup()

    def test_verify_failure_removes_secret(self):
        temp, admin, runtime = self._paths()
        calls = []

        def runner(command, *, check, cwd):
            name = Path(command[1]).name
            calls.append(name)
            if name == "provision_daily_runtime.py":
                runtime.write_text("d" * 48, encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)
            raise subprocess.CalledProcessError(7, command)

        try:
            with self.assertRaises(subprocess.CalledProcessError):
                _run_steps(
                    target_port=55432,
                    live_port=5432,
                    admin_secret_file=admin,
                    runtime_secret_file=runtime,
                    settings_source_port=None,
                    runner=runner,
                )
            self.assertEqual(
                calls,
                ["provision_daily_runtime.py", "verify_production_runtime.py"],
            )
            self.assertFalse(runtime.exists())
        finally:
            temp.cleanup()

    def test_live_port_is_rejected_before_subprocess(self):
        temp, admin, _runtime = self._paths()
        called = []

        def runner(command, *, check, cwd):
            called.append(command)

        try:
            with self.assertRaisesRegex(RuntimeError, "live PostgreSQL port"):
                run_rehearsal(
                    target_port=5432,
                    live_port=5432,
                    admin_secret_file=admin,
                    runner=runner,
                )
            self.assertEqual(called, [])
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
