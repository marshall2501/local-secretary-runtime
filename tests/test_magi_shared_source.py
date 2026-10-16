# Provenance-Origin: original
# Provenance-AI: AI-generated
"""NiceGUIから分離したSource読取処理の権限・出力契約を隔離検証する。

PKB/Web/家計の実DBや外部検索を使わず、Scope外の情報拡張がないことを確認する。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from ritsuko.application.pkb_observation import execute_magi_pkb_request
from ritsuko.application.source_execution import execute_magi_source_request


class SharedSourceTests(unittest.TestCase):
    """RITSUKOが使用するPKB・Web・Financeの共通Source経路を検査する。"""

    def test_unresolved_pkb_component_never_broadens_search(self):
        """要求されたPCが見つからない場合は、全PKB検索へ迂回しない。"""
        called = []

        class FakePkb:
            """未解決時には個別Entity読取が呼ばれないことを調べるための代役。"""

            def resolve_component_reference(self, *_args):
                """正本に部品関係がない状況を模擬する。"""
                called.append("resolve")
                return None

        result = execute_magi_pkb_request(
            "メインPCのGPUを教えて",
            {"source": "pkb", "what": "メインPCのGPUモデル"},
            pkb_repository=FakePkb(),
            entity_map=lambda: {},
            execute_read=lambda *_args: called.append("broad_read"),
        )
        self.assertEqual(result["result"]["result_kind"], "pkb_target_unresolved")
        self.assertEqual(called, [])

    def test_web_without_policy_cannot_call_external_search(self):
        """保存済み権限SnapshotなしではWeb検索Providerを呼ばない。"""
        called = []

        async def native(*_args):
            """呼ばれたら外部実行を試みたと検出するための代役。"""
            called.append("external_search")
            return {"status": "ok"}

        result = execute_magi_source_request(
            "公開情報の調査",
            {"source": "web", "what": "Python dictionary get documentation"},
            {"policy_snapshot": None, "member_specs": []},
            pkb_execute=lambda *_args: called.append("pkb"),
            execute_read=lambda *_args: called.append("fallback"),
            native_web_search=native,
        )
        self.assertEqual(result["result"]["result_kind"], "web_search_permission_denied")
        self.assertEqual(called, [])

    def test_finance_read_uses_existing_capability(self):
        """家計の情報源は従来Capabilityを呼び、Source種別を明示する。"""
        calls = []

        def execute(capability: str, query: str) -> dict:
            """Capability名と家計対象月を記録して結果を返す。"""
            calls.append((capability, query))
            return {"status": "ok", "result": {"result_kind": "summary"}}

        result = execute_magi_source_request(
            "家計の概要",
            {"source": "finance", "what": "2026年8月の支出"},
            {"user_raw": "家計の概要", "member_specs": []},
            pkb_execute=lambda *_args: None,
            execute_read=execute,
            native_web_search=lambda *_args: None,
        )
        self.assertEqual(calls, [("finance_read", "2026年8月の支出")])
        self.assertEqual(result["source"], "finance")

    def test_allowed_web_fallback_reuses_same_public_query(self):
        """Native検索失敗後の代替経路でも、公開クエリを加工して増やさない。"""
        from unittest.mock import patch

        calls = []

        async def native(query: str, member_specs: list[dict]) -> dict:
            """公開クエリの受領を記録し、検索失敗だけを模倣する。"""
            calls.append(("native", query))
            return {"status": "failed", "result": {"member_runs": []}}

        def core_read(capability: str, query: str) -> dict:
            """既存検索へ流れる公開クエリを記録する。"""
            calls.append(("fallback", query))
            return {"status": "ok", "result": {}, "source_metadata": {}}

        with patch(
            "ritsuko.application.source_execution.public_web_search_permission",
            return_value={"allowed": True},
        ):
            result = execute_magi_source_request(
                "Pythonの辞書仕様を調べて",
                {"source": "web", "what": "Python dict.get documentation"},
                {"user_raw": "Pythonの辞書仕様を調べて", "member_specs": []},
                pkb_execute=lambda *_args: None,
                execute_read=core_read,
                native_web_search=native,
            )
        self.assertEqual(calls, [
            ("native", "Python dict.get documentation"),
            ("fallback", "Python dict.get documentation"),
        ])
        self.assertEqual(result["source_metadata"]["native_web_fallback"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
