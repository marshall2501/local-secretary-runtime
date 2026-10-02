from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pkb_proto.system_debug import environment_snapshot, git_snapshot


class SystemDebugTests(TestCase):
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
