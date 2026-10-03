from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from ritsuko.core.core_advisor import (
    AdvisorResult,
    advise,
    choose_model,
    diagnose_response,
    inspect_output,
)
from interfaces.web.app import (
    _recover_interrupted_core_advisors,
    _restore_interrupted_cooperative_probe,
    _run_core_advisor_shadow,
)


class CoreAdvisorTests(unittest.TestCase):
    def test_choose_model_prefers_llama_before_qwen(self):
        self.assertEqual(
            choose_model(["qwen3.5:9b", "llama3.1:8b"]),
            "llama3.1:8b",
        )

    def test_choose_model_honors_runtime_selection(self):
        self.assertEqual(
            choose_model(["llama3.1:8b", "gemma4:12b"], "gemma4:12b"),
            "gemma4:12b",
        )

    def test_choose_model_rejects_uninstalled_runtime_selection(self):
        with self.assertRaises(ValueError):
            choose_model(["llama3.1:8b"], "gemma4:12b")

    def test_valid_matching_observe_proposal(self):
        result = inspect_output(
            {
                "situation": "PKB現在値とWeb最新値の比較が必要です。",
                "missing_information": [],
                "next_step": "observe",
                "proposed_action": "pkb_web_compare",
                "reason": "現在値と公開情報の両方が必要です。",
                "expected_result": "両者の比較結果",
            },
            {"pkb_search", "web_research", "pkb_web_compare"},
            "pkb_web_compare",
            "observe",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.comparison, "match")
        self.assertEqual(result.next_step, "observe")
        self.assertEqual(result.proposed_action, "pkb_web_compare")

    def test_valid_mismatch_is_observed_not_rejected(self):
        result = inspect_output(
            {
                "situation": "Webだけでよいと判断しました。",
                "missing_information": [],
                "next_step": "observe",
                "proposed_action": "web_research",
                "reason": "公開情報が必要です。",
                "expected_result": "Web調査結果",
            },
            {"pkb_search", "web_research", "pkb_web_compare"},
            "pkb_web_compare",
            "observe",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.comparison, "mismatch")
        self.assertEqual(result.proposed_action, "web_research")

    def test_response_diagnostic_keeps_only_contract_fields(self):
        diagnostic = diagnose_response(
            '{"situation":"ok","missing_information":[],"next_step":"observe",'
            '"proposed_action":"pkb_search","reason":"use PKB","expected_result":null,'
            '"private_reasoning":"do not keep"}'
        )
        self.assertTrue(diagnostic["json_valid"])
        self.assertEqual(
            diagnostic["safe_response"]["expected_result"],
            None,
        )
        self.assertEqual(
            diagnostic["field_types"]["expected_result"],
            "NoneType",
        )
        self.assertEqual(
            diagnostic["unexpected_keys"],
            ["private_reasoning"],
        )
        self.assertNotIn(
            "private_reasoning",
            diagnostic["safe_response"],
        )

    def test_response_diagnostic_does_not_store_non_json_prose(self):
        diagnostic = diagnose_response("thinking aloud and then maybe JSON")
        self.assertFalse(diagnostic["json_valid"])
        self.assertIsNone(diagnostic["safe_response"])
        self.assertEqual(diagnostic["raw_length"], 34)

    def test_invalid_json_is_fail_closed(self):
        result = inspect_output(
            "not json",
            {"pkb_search", "web_research"},
            "pkb_search",
            "observe",
        )
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.comparison, "invalid")
        self.assertEqual(result.error, "invalid_json")

    def test_unknown_capability_is_invalid(self):
        result = inspect_output(
            {
                "situation": "何か実行します。",
                "missing_information": [],
                "next_step": "observe",
                "proposed_action": "delete_everything",
                "reason": "test",
                "expected_result": "test",
            },
            {"pkb_search", "web_research"},
            "pkb_search",
            "observe",
        )
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error, "unknown_capability")

    def test_clarify_can_match_melchior_clarification_state(self):
        result = inspect_output(
            {
                "situation": "対象が不足しています。",
                "missing_information": ["対象GPU"],
                "next_step": "clarify",
                "proposed_action": None,
                "reason": "対象を特定できません。",
                "expected_result": "追加情報",
            },
            {"pkb_search", "web_research"},
            None,
            "clarify",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.comparison, "match")
        self.assertEqual(result.next_step, "clarify")
        self.assertIsNone(result.proposed_action)

    def test_respond_requires_no_capability_or_missing_information(self):
        result = inspect_output(
            {
                "situation": "観測済み情報で回答できます。",
                "missing_information": [],
                "next_step": "respond",
                "proposed_action": None,
                "reason": "追加取得は不要です。",
                "expected_result": "現在値の回答",
            },
            {"pkb_search", "web_research"},
            "pkb_search",
            "observe",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.next_step, "respond")
        self.assertEqual(result.comparison, "mismatch")

    def test_observe_rejects_optional_missing_information(self):
        result = inspect_output(
            {
                "situation": "家計を集計できます。",
                "missing_information": ["表示形式"],
                "next_step": "observe",
                "proposed_action": "finance_read",
                "reason": "家計データを読みます。",
                "expected_result": "9月支出",
            },
            {"finance_read"},
            "finance_read",
            "observe",
        )
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error, "non_clarify_missing_information")

    @patch("interfaces.web.app._write_core_advisor_shadow", return_value=True)
    @patch("interfaces.web.app.advise_core")
    @patch("interfaces.web.app.choose_advisor_model", return_value="gemma3:12b")
    @patch("interfaces.web.app.list_advisor_models", return_value=["gemma3:12b"])
    def test_async_worker_records_running_then_completed(
        self, _models, _choose, advise_mock, write_mock
    ):
        advise_mock.return_value = AdvisorResult(
            "ok",
            "match",
            model="gemma3:12b",
            situation="PKB lookup is sufficient.",
            next_step="observe",
            proposed_action="pkb_search",
            reason="Use local PKB.",
            expected_result="Current driver",
            timeout_seconds=600,
        )
        from uuid import UUID

        task_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        _run_core_advisor_shadow(
            task_id,
            "メインPCのGPUの現在のドライバーを調べて",
            {
                "member": "MELCHIOR",
                "status": "ready",
                "selected_capability": "pkb_search",
            },
            {"version": "magi_observation_v1", "matched_entities": []},
            "gemma3:12b",
            600,
        )
        self.assertEqual(write_mock.call_count, 2)
        running = write_mock.call_args_list[0].args[1]
        final = write_mock.call_args_list[1].args[1]
        self.assertEqual(running["job_status"], "running")
        self.assertEqual(running["model"], "gemma3:12b")
        self.assertEqual(final["job_status"], "completed")
        self.assertEqual(final["comparison"], "match")
        self.assertEqual(final["synthesis"]["next_step"], "observe")
        self.assertEqual(final["synthesis"]["selected_capability"], "pkb_search")
        self.assertEqual(final["model"], "gemma3:12b")
        self.assertEqual(final["timeout_seconds"], 600)
        self.assertIsNotNone(final["elapsed_seconds"])
        advise_kwargs = advise_mock.call_args.kwargs
        self.assertEqual(
            advise_kwargs["observations"]["version"],
            "magi_observation_v1",
        )
        self.assertEqual(advise_kwargs["melchior_next_step"], "observe")
        self.assertEqual(advise_kwargs["task_state"], "received")

    @patch("interfaces.web.app._write_core_advisor_shadow", return_value=True)
    @patch("interfaces.web.app.advise_core")
    @patch("interfaces.web.app.choose_advisor_model", return_value="gemma3:12b")
    @patch("interfaces.web.app.list_advisor_models", return_value=["gemma3:12b"])
    def test_async_worker_preserves_timeout_metadata(
        self, _models, _choose, advise_mock, write_mock
    ):
        advise_mock.return_value = AdvisorResult(
            "unavailable",
            "unavailable",
            model="gemma3:12b",
            error="TimeoutError",
            timeout_seconds=900,
        )
        from uuid import UUID

        _run_core_advisor_shadow(
            UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            "test",
            {
                "member": "MELCHIOR",
                "status": "ready",
                "selected_capability": "pkb_search",
            },
            {"version": "magi_observation_v1", "matched_entities": []},
            "gemma3:12b",
            900,
        )
        final = write_mock.call_args_list[-1].args[1]
        self.assertEqual(final["job_status"], "timeout")
        self.assertEqual(final["model"], "gemma3:12b")
        self.assertEqual(final["timeout_seconds"], 900)
        self.assertEqual(final["error"], "TimeoutError")


    @patch("interfaces.web.app._finalize_cooperative_probe")
    @patch(
        "interfaces.web.app._record_cooperative_probe",
        return_value=(
            __import__("uuid").UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            __import__("uuid").UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        ),
    )
    @patch(
        "interfaces.web.app._execute_cooperative_local_probe",
        return_value={
            "capability": "pkb_search",
            "result": {
                "result_kind": "components",
                "items": [{"component_name": "GPU1"}],
                "total": 1,
            },
            "answer": "PKBの構成記録では、GPU1。",
            "total": 1,
            "tool": "pkb",
            "operation": "search",
            "source_slug": "pkb-search",
            "citation": "test",
            "verified_by": "deterministic_pkb_query",
        },
    )
    @patch("interfaces.web.app._claim_cooperative_probe", return_value=True)
    @patch("interfaces.web.app._write_core_advisor_shadow", return_value=True)
    @patch("interfaces.web.app.advise_core")
    @patch("interfaces.web.app.choose_advisor_model", return_value="gemma3:12b")
    @patch("interfaces.web.app.list_advisor_models", return_value=["gemma3:12b"])
    def test_ambiguous_worker_executes_synthesized_pkb_probe_then_reorients(
        self,
        _models,
        _choose,
        advise_mock,
        write_mock,
        claim_mock,
        execute_mock,
        record_mock,
        finalize_mock,
    ):
        advise_mock.side_effect = [
            AdvisorResult(
                "ok",
                "mismatch",
                model="gemma3:12b",
                situation="Main PC is known locally.",
                next_step="observe",
                proposed_action="pkb_web_compare",
                reason="Compare local and public information.",
                expected_result="Comparison",
                timeout_seconds=900,
            ),
            AdvisorResult(
                "ok",
                "mismatch",
                model="gemma3:12b",
                situation="Local PKB evidence is now available.",
                next_step="respond",
                proposed_action=None,
                reason="The bounded local observation is enough for an initial answer.",
                expected_result="Local summary",
                timeout_seconds=900,
            ),
        ]
        from uuid import UUID

        task_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
        _run_core_advisor_shadow(
            task_id,
            "メインPCについて調べて",
            {
                "member": "MELCHIOR",
                "status": "question",
                "selected_capability": None,
            },
            {
                "version": "magi_observation_v1",
                "matched_entities": [{"name": "メインPC"}],
            },
            "gemma3:12b",
            900,
        )

        self.assertEqual(advise_mock.call_count, 2)
        self.assertEqual(
            advise_mock.call_args_list[0].kwargs["task_state"],
            "received",
        )
        self.assertEqual(
            advise_mock.call_args_list[1].kwargs["task_state"],
            "observed",
        )
        self.assertEqual(
            advise_mock.call_args_list[1].kwargs["observations"]["version"],
            "magi_observation_v2",
        )
        claim_mock.assert_called_once_with(task_id, "pkb_search")
        execute_mock.assert_called_once_with(
            "pkb_search",
            "メインPCについて調べて",
            {
                "version": "magi_observation_v1",
                "matched_entities": [{"name": "メインPC"}],
            },
        )
        record_mock.assert_called_once()
        finalize_mock.assert_called_once()
        final_decision = finalize_mock.call_args.args[1]
        self.assertEqual(final_decision["next_step"], "respond")
        final_shadow = write_mock.call_args_list[-1].args[1]
        self.assertEqual(final_shadow["job_status"], "completed")
        self.assertEqual(final_shadow["cooperative_execution"]["capability"], "pkb_search")
        self.assertEqual(final_shadow["cooperative_execution"]["final_next_step"], "respond")
        self.assertEqual(len(final_shadow["cycles"]), 2)

    def test_interrupted_probe_restores_waiting_external_state(self):
        from uuid import UUID

        task_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        checkpoint = {
            "core_slice": "daily_read_only_v1",
            "phase": "act",
            "selected_capability": "pkb_search",
            "question": None,
            "reason": "magi_cooperative_probe",
            "cooperative_cycle": 1,
            "cooperative_resume": {
                "phase": "awaiting_clarification",
                "selected_capability": None,
                "question": "元の確認質問",
                "reason": "ambiguous_request",
            },
        }
        with patch("bootstrap.web_runtime.connection") as connection:
            db = connection.return_value.__enter__.return_value
            cur = db.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = ("running", checkpoint)

            restored = _restore_interrupted_cooperative_probe(
                task_id, "interrupted_by_server_shutdown"
            )

        self.assertTrue(restored)
        update = next(
            call for call in cur.execute.call_args_list
            if "SET status='waiting_external'" in call.args[0]
        )
        restored_checkpoint = update.args[1][0].obj
        self.assertEqual(restored_checkpoint["phase"], "awaiting_clarification")
        self.assertEqual(restored_checkpoint["question"], "元の確認質問")
        self.assertEqual(restored_checkpoint["reason"], "ambiguous_request")
        self.assertTrue(restored_checkpoint["cooperative_interrupted"])
        self.assertEqual(
            restored_checkpoint["cooperative_error"],
            "interrupted_by_server_shutdown",
        )
        self.assertTrue(any(
            "core.advisor.task_restored" in call.args[0]
            for call in cur.execute.call_args_list
        ))

    @patch("interfaces.web.app._restore_interrupted_cooperative_probe")
    @patch("interfaces.web.app._write_core_advisor_shadow", return_value=True)
    @patch("bootstrap.web_runtime.connection")
    def test_recovery_marks_shadow_and_repairs_running_probe(
        self, connection, write_mock, restore_mock
    ):
        from uuid import UUID

        task_id = UUID("eeeeeeee-ffff-ffff-ffff-ffffffffffff")
        db = connection.return_value.__enter__.return_value
        cur = db.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [(
            task_id,
            {
                "job_status": "running",
                "started_at": None,
                "elapsed_seconds": 1.0,
            },
        )]

        recovered = _recover_interrupted_core_advisors(
            "interrupted_by_server_shutdown"
        )

        self.assertEqual(recovered, 1)
        shadow = write_mock.call_args.args[1]
        self.assertEqual(shadow["job_status"], "error")
        self.assertEqual(shadow["error"], "interrupted_by_server_shutdown")
        restore_mock.assert_called_once_with(
            task_id, "interrupted_by_server_shutdown"
        )


    @patch("ritsuko.core.core_advisor.list_chat_models", return_value=["llama3.1:8b"])
    @patch("ritsuko.core.core_advisor.urlopen", side_effect=URLError("offline"))
    def test_provider_error_never_raises_into_core(self, _urlopen, _models):
        result = advise(
            "メインPCのGPUを調べて",
            current_selection="pkb_search",
            melchior_next_step="observe",
            task_state="received",
            timeout=0.1,
            model="llama3.1:8b",
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.comparison, "unavailable")
        self.assertEqual(result.model, "llama3.1:8b")
        self.assertEqual(result.timeout_seconds, 0.1)
        self.assertEqual(result.error, "URLError")
        self.assertEqual(result.request_context["task_state"], "received")
        self.assertNotIn("task_status", result.request_context)


if __name__ == "__main__":
    unittest.main()
