# Provenance-Origin: original
# Provenance-AI: AI-generated
"""実機用MAGI読取プローブのパス設定と表示範囲を、実DBなしで検証する。"""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.db.probe_magi_runtime_readonly import (
    configure_daily_environment,
    member_states,
)


class MagiRuntimeReadOnlyProbeTests(unittest.TestCase):
    """プローブがSecret値やTask本文を扱わず3席の有効状態だけ示すことを確認する。"""

    def test_member_states_separate_disabled_and_missing(self) -> None:
        """有効・無効・未登録を混同せず、接続URLやモデル名を結果へ含めない。"""
        states = member_states(
            [
                {"name": "MELCHIOR", "enabled": True, "model": "private-local-name"},
                {"name": "CASPER", "enabled": False, "endpoint": "private-endpoint"},
            ],
            ("MELCHIOR", "CASPER", "BALTHASAR"),
        )
        self.assertEqual(states, [
            ("MELCHIOR", "ENABLED"),
            ("CASPER", "DISABLED"),
            ("BALTHASAR", "NOT_ASSIGNED"),
        ])

    def test_existing_database_config_sets_paths_not_secret_values(self) -> None:
        """既存のPortとSecretファイルパスのみを現在プロセスへ設定する。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.postgres").write_text("LSA_DB_PORT=55444\n", encoding="utf-8")
            secret = root / "secrets" / "secretary-daily-runtime-password.txt"
            secret.parent.mkdir()
            secret.write_text("not-a-real-password", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                configure_daily_environment(root)
                self.assertEqual(os.environ["LSA_DAILY_DB_NAME"], "secretary")
                self.assertEqual(os.environ["LSA_PKB_DAILY_PORT"], "55444")
                self.assertEqual(os.environ["LSA_PKB_DAILY_SECRET"], str(secret))
                self.assertNotIn("not-a-real-password", os.environ.values())

    def test_missing_secret_file_is_rejected(self) -> None:
        """Secretファイルが存在しなければ、DB接続へ進まず停止する。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env.postgres").write_text("LSA_DB_PORT=55444\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "secret file missing"):
                configure_daily_environment(root)


if __name__ == "__main__":
    unittest.main()
