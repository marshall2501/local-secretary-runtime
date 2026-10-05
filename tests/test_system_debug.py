from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from infrastructure.system_debug import environment_snapshot, git_snapshot
from infrastructure.postgres.pkb_debug import debug_database_summary


class _ClosedInfo:
    def __init__(self, db):
        self._db = db

    @property
    def host(self):
        if self._db.closed:
            raise RuntimeError("connection already closed")
        return "127.0.0.1"

    @property
    def port(self):
        if self._db.closed:
            raise RuntimeError("connection already closed")
        return 55432


class _DebugCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query):
        return None

    def fetchone(self):
        return ("secretary", "secretary_daily_runtime")


class _DebugDb:
    def __init__(self):
        self.closed = False
        self.info = _ClosedInfo(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def cursor(self):
        return _DebugCursor()


class SystemDebugTests(TestCase):
    def test_top_database_summary_reads_connection_info_before_close(self):
        db = _DebugDb()

        result = debug_database_summary(
            lambda: db,
            expected_database="secretary",
            expected_user="secretary_daily_runtime",
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["boundary_ok"])
        self.assertEqual(result["host"], "127.0.0.1")
        self.assertEqual(result["port"], 55432)
        self.assertTrue(db.closed)

    def test_environment_snapshot_masks_secret_values(self):
        env = {
            "LSA_PKB_DAILY_PORT": "55432",
            "OLLAMA_HOST": "http://127.0.0.1:11434",
            "OPENAI_API_KEY": "must-never-appear",
            "OPENAI_ADMIN_KEY": "also-secret",
            "GEMINI_API_KEY": "",
            "LSA_PKB_DAILY_SECRET": "Z:/missing/secret.txt",
        }
        rows = {row["name"]: row for row in environment_snapshot(env)}

        self.assertEqual(rows["LSA_PKB_DAILY_PORT"]["value"], "55432")
        self.assertEqual(
            rows["OLLAMA_HOST"]["value"],
            "http://127.0.0.1:11434",
        )
        self.assertEqual(rows["OPENAI_API_KEY"]["value"], "SET")
        self.assertEqual(rows["OPENAI_ADMIN_KEY"]["value"], "SET")
        self.assertEqual(rows["GEMINI_API_KEY"]["value"], "NOT SET")
        self.assertEqual(
            rows["LSA_PKB_DAILY_SECRET"]["value"],
            "SET / file missing",
        )

        rendered = repr(rows)
        self.assertNotIn("must-never-appear", rendered)
        self.assertNotIn("also-secret", rendered)
        self.assertNotIn("Z:/missing/secret.txt", rendered)

    def test_git_snapshot_reads_loose_head_without_running_git(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            git = root / ".git"
            ref = git / "refs" / "heads"
            ref.mkdir(parents=True)
            (git / "HEAD").write_text(
                "ref: refs/heads/main\n",
                encoding="utf-8",
            )
            (ref / "main").write_text(
                "0123456789abcdef0123456789abcdef01234567\n",
                encoding="utf-8",
            )

            result = git_snapshot(root)

        self.assertEqual(result["branch"], "main")
        self.assertEqual(
            result["commit"],
            "0123456789abcdef0123456789abcdef01234567",
        )
