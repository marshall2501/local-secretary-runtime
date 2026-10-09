"""1047: agent-owned Task clock, legacy compatibility and Finance calendar guard."""
from __future__ import annotations

from datetime import date, datetime, timezone
import unittest
from zoneinfo import ZoneInfo

from ritsuko.application.observation_sources import finance_query_from_request
from ritsuko.magi.async_execution import (
    _send_async,
    start_dialogue_async,
)
from ritsuko.magi.dialogue import _send, start_dialogue, COMMON_INSTRUCTIONS
from ritsuko.magi.runtime_context import (
    create_runtime_context,
    runtime_context_from_session,
    task_reference_date,
)


SPECS = [
    {"name": "MELCHIOR", "provider": "ollama", "model": "fixture-local",
     "enabled": True, "weight": 1},
    {"name": "CASPER", "provider": "openai", "model": "fixture-cloud",
     "enabled": True, "weight": 2},
    {"name": "BALTHASAR", "provider": "gemini", "model": "fixture-gemini",
     "enabled": True, "weight": 1},
]


class TaskClockContractTests(unittest.TestCase):
    def test_task_reference_is_aware_with_named_timezone(self):
        reference = datetime(2026, 10, 8, 1, 47, tzinfo=timezone.utc)
        ctx = create_runtime_context(now=reference)
        self.assertEqual(ctx, {
            "reference_datetime": "2026-10-08T10:47:00+09:00",
            "timezone": "Asia/Tokyo",
            "locale": "ja-JP",
        })
        self.assertEqual(task_reference_date({"runtime_context": ctx}), date(2026, 10, 8))
        self.assertIn("runtime_context", COMMON_INSTRUCTIONS)
        self.assertIn("モデルの学習時点", COMMON_INSTRUCTIONS)

    def test_timezone_boundary_and_legacy_no_backfill(self):
        ref = datetime(2026, 1, 1, 0, 10, tzinfo=ZoneInfo("Asia/Tokyo"))
        ctx = create_runtime_context(now=ref)
        self.assertEqual(task_reference_date({"runtime_context": ctx}), date(2026, 1, 1))
        self.assertIsNone(runtime_context_from_session({"turns": []}))
        self.assertIsNone(task_reference_date({"turns": []}))
        with self.assertRaisesRegex(ValueError, "naive"):
            runtime_context_from_session({
                "runtime_context": {"reference_datetime": "2026-01-01T00:10:00",
                                    "timezone": "Asia/Tokyo", "locale": "ja-JP"},
            })
        with self.assertRaises(ValueError):
            create_runtime_context(now=datetime(2026, 1, 1))

    def test_sync_magi_request_reuses_session_clock(self):
        seen = []
        def stub(envelope, **_kwargs):
            seen.append(envelope)
            return {"status": "unavailable", "response": None, "errors": ["fixture"]}

        session = start_dialogue("先月の家計を分析して", member_specs=SPECS, caller=stub)
        self.assertEqual(session["prompt_version"], session["turns"][0]["request_envelope"]["prompt_version"])
        self.assertEqual(seen[0]["runtime_context"], session["runtime_context"])
        self.assertNotIn("resource_catalog", seen[0])
        old = session["runtime_context"]["reference_datetime"]
        _send(session, "analyze", "evaluate_observation", "fixture", stub, timeout=1)
        self.assertEqual(seen[1]["runtime_context"]["reference_datetime"], old)
        self.assertEqual(len(seen), 2)

    def test_finance_month_uses_fixed_task_clock_across_month_rollover(self):
        ctx = create_runtime_context(
            now=datetime(2026, 10, 31, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
        )
        session = {"user_raw": "先月の家計を教えて", "runtime_context": ctx}
        query = finance_query_from_request(
            {"what": "先月の家計の収支"}, today=date(2026, 11, 1), session=session
        )
        self.assertTrue(query.startswith("2026年9月 "), query)
        self.assertTrue(finance_query_from_request(
            {"what": "先月"}, today=date(2026, 11, 1)
        ).startswith("2026年10月 "))
        jan = create_runtime_context(
            now=datetime(2026, 1, 1, 0, 5, tzinfo=ZoneInfo("Asia/Tokyo"))
        )
        self.assertTrue(finance_query_from_request(
            {"what": "先月"}, session={"runtime_context": jan, "user_raw": "先月"}
        ).startswith("2025年12月 "))

    def test_reject_model_generated_conflicting_calendar_month(self):
        ctx = create_runtime_context(
            now=datetime(2026, 10, 8, tzinfo=ZoneInfo("Asia/Tokyo"))
        )
        session = {"runtime_context": ctx, "user_raw": "先月の家計を調べて"}
        with self.assertRaisesRegex(ValueError, "finance_query_conflicts_task_reference_period"):
            finance_query_from_request({"what": "2025年8月の家計"}, session=session)
        self.assertEqual(
            finance_query_from_request({"what": "2026年9月の家計"}, session=session),
            "2026年9月の家計",
        )
        # Explicit comparisons in the user's own request are not rewritten.
        comparison = {"runtime_context": ctx, "user_raw": "先月と2025年8月の家計を比較"}
        self.assertEqual(
            finance_query_from_request({"what": "2025年8月の家計"}, session=comparison),
            "2025年8月の家計",
        )


class AsyncTaskClockContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_request_and_follow_up_share_task_clock(self):
        seen = []
        async def stub(envelope, **_kwargs):
            seen.append(envelope)
            return {"status": "unavailable", "response": None, "errors": ["fixture"]}

        session = await start_dialogue_async(
            "先月の家計を分析して", member_specs=SPECS, caller=stub,
            task_id="test-datetime-1047"
        )
        self.assertEqual(seen[0]["runtime_context"], session["runtime_context"])
        self.assertEqual(seen[0]["task_id"], session["task_id"])
        self.assertNotIn("observations", seen[0])
        await _send_async(
            session, "analyze", "evaluate_observation", "fixture",
            timeout=1, caller=stub,
        )
        self.assertEqual(seen[1]["runtime_context"], session["runtime_context"])
        self.assertEqual(session["turns"][1]["request_envelope"]["runtime_context"],
                         session["runtime_context"])
        self.assertIn("observations", seen[1])

    async def test_legacy_async_session_does_not_invent_datetime(self):
        async def stub(envelope, **_kwargs):
            self.assertNotIn("runtime_context", envelope)
            return {"status": "unavailable", "response": None, "errors": ["fixture"]}

        session = {
            "task_id": "legacy", "user_raw": "テスト",
            "prompt_version": "d19-state-driven-v4", "turns": [],
            "observations": [], "member_specs": SPECS, "model": "",
        }
        await _send_async(session, "classify", "classify", "fixture",
                          timeout=1, caller=stub)
        self.assertNotIn("runtime_context", session)
        self.assertNotIn("runtime_context", session["turns"][0]["request_envelope"])


    async def test_task_checkpoint_resume_keeps_month_end_clock_and_legacy_compatible(self):
        """JSON-backed Task checkpoint -> user-resume -> MAGI Turn, without DB or providers."""
        import json
        from uuid import UUID
        from unittest.mock import patch

        from ritsuko.core.observation_loop import resume_user_answer
        from ritsuko.magi.async_execution import continue_with_user_clarification_async

        task_id = UUID("11111111-1111-4111-8111-111111111111")
        frozen = create_runtime_context(
            now=datetime(2026, 10, 31, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
        )
        self.assertEqual(frozen["reference_datetime"], "2026-10-31T23:59:00+09:00")

        for label, context, expected_month in (
            ("new_task", frozen, "2026年9月"),
            ("legacy_task", None, "2026年10月"),
        ):
            with self.subTest(label=label):
                # Simulate the persisted JSONB checkpoint restored after November 1.
                session = {
                    "task_id": str(task_id),
                    "user_raw": "先月の家計について",
                    "model": "",
                    "prompt_version": "d19-state-driven-v5-datetime" if context else "d19-state-driven-v4",
                    "member_specs": SPECS,
                    "status": "waiting_user",
                    "next_step": "ask_user_for_information",
                    "classification": {
                        "category": "INFORMATION",
                        "understood_request": "先月の家計について知りたい",
                        "reason": "fixture",
                        "confidence": "high",
                        "multiple_requests": False,
                    },
                    "detail": {"state": "NEED_CLARIFICATION", "question_for_user": "詳細は？"},
                    "observations": [],
                    "pending_requests": [{"request_id": "REQ-1", "source": "user", "what": "詳細"}],
                    "previous_request_signatures": [],
                    "conversation_context": [],
                    "user_question": "詳細は？",
                    "magi_disagreement": None,
                    "user_source_reviewed": False,
                    "last_question_purpose": "identify_missing_information",
                    "turns": [
                        {"stage": "classify", "request_envelope": {"turn": 1}},
                        {"stage": "analyze", "request_envelope": {"turn": 2}},
                    ],
                    "turn_limit": 4,
                    "legacy_router_used": False,
                    "tool_read_executed": False,
                }
                if context is not None:
                    session["runtime_context"] = context
                checkpoint_json = json.dumps({"magi_session": session}, ensure_ascii=False)
                captured, persisted, aborted = [], [], []

                def claim(claimed_id, reply_length, reply_fingerprint):
                    self.assertEqual(claimed_id, task_id)
                    self.assertEqual(reply_length, len("支出について"))
                    self.assertEqual(len(reply_fingerprint), 64)
                    return json.loads(checkpoint_json)["magi_session"], "finance_read"

                async def caller(envelope, *, model, timeout, member_specs):
                    captured.append(envelope)
                    self.assertEqual(len(member_specs), 3)
                    return {
                        "status": "ok",
                        "response": {
                            "understood_request": "先月の家計について知りたい",
                            "state": "READY",
                            "reason": "本人が補足したので応答できる",
                            "information_requests": [],
                            "question_for_user": None,
                            "answer_candidate": "追加情報を確認しました。",
                            "knowledge_candidate": None,
                            "action_candidate": None,
                        },
                        "errors": [],
                    }

                async def continue_with_stub(saved, text, **kwargs):
                    return await continue_with_user_clarification_async(
                        saved, text, caller=caller, **kwargs
                    )

                def persist(saved_id, updated_session, capability):
                    self.assertEqual(saved_id, task_id)
                    self.assertEqual(capability, "finance_read")
                    persisted.append(json.loads(json.dumps(updated_session, ensure_ascii=False)))

                def abort(*args):
                    aborted.append(args)

                # Resume must not call the new-Task constructor to replace the old clock.
                with patch("ritsuko.magi.async_execution.create_runtime_context",
                           side_effect=AssertionError("resume must not set a new clock")):
                    result = await resume_user_answer(
                        task_id,
                        "支出について",
                        timeout=10,
                        claim_user_resume_record=claim,
                        persist_session_record=persist,
                        abort_user_resume_record=abort,
                        user_continuation=continue_with_stub,
                    )

                self.assertFalse(aborted)
                self.assertEqual(len(captured), 1)
                self.assertEqual(result["status"], "candidate_ready")
                self.assertEqual(result["task_id"], str(task_id))
                self.assertEqual(len(result["turns"]), 3)
                self.assertEqual(captured[0]["turn"], 3)
                self.assertEqual(len(persisted), 1)
                self.assertEqual(persisted[0]["task_id"], str(task_id))
                self.assertEqual(
                    finance_query_from_request(
                        {"what": "先月の支出"}, session=persisted[0],
                        today=date(2026, 11, 1),
                    ).startswith(expected_month + " "),
                    True,
                )
                if context is None:
                    self.assertNotIn("runtime_context", result)
                    self.assertNotIn("runtime_context", captured[0])
                else:
                    self.assertEqual(result["runtime_context"], frozen)
                    self.assertEqual(captured[0]["runtime_context"], frozen)
                    self.assertEqual(persisted[0]["runtime_context"], frozen)
                    self.assertEqual(
                        result["turns"][2]["request_envelope"]["runtime_context"],
                        frozen,
                    )


if __name__ == "__main__":
    unittest.main()
