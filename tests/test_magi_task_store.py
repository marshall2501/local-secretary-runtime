from __future__ import annotations

import unittest
from uuid import UUID

from infrastructure.postgres.magi_task_repository import (
    abort_proposal_review,
    abort_user_resume,
    claim_proposal_review,
    claim_user_resume,
    create_task,
    reserve_start,
    claim_start,
    list_pending_starts,
    request_stop,
    finalize_proposal_review,
    persist_session,
    prepare_memory_intake,
    record_pkb_read,
    record_source_read,
)


TASK_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTION_ID = UUID("22222222-2222-4222-8222-222222222222")
RESULT_ID = UUID("33333333-3333-4333-8333-333333333333")
SOURCE_ID = UUID("44444444-4444-4444-8444-444444444444")


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Cursor(_Context):
    def __init__(self, fetches=()):
        self.fetches = list(fetches)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchone(self):
        if not self.fetches:
            return None
        return self.fetches.pop(0)

    def fetchall(self):
        """列挙APIの最小cursorシミュレーション。"""
        result = list(self.fetches)
        self.fetches.clear()
        return result


class _DB:
    def __init__(self, fetches=()):
        self.cur = _Cursor(fetches)

    def transaction(self):
        return _Context()

    def cursor(self):
        return self.cur


class MagiTaskStoreTests(unittest.TestCase):
    @staticmethod
    def review_session():
        return {
            "task_id": str(TASK_ID),
            "status": "proposal_ready",
            "next_step": "review_proposal",
            "tool_read_executed": True,
            "detail": {
                "state": "KNOWLEDGE_CANDIDATE",
                "reason": "本人回答で不足情報が解消した",
                "answer_candidate": "メインPCのGPUはRadeon RX 9070 XTです。",
                "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
            },
            "observations": [
                {
                    "source": "pkb",
                    "verified": True,
                    "confidentiality": "private",
                    "text": "model unavailable",
                },
                {
                    "source": "user_clarification",
                    "verified": False,
                    "text": "Radeon RX 9070 XT",
                    "responds_to": ["REQ-1"],
                },
            ],
            "turns": [{"question_purpose": "evaluate_observation", "status": "ok"}],
        }

    @classmethod
    def review_checkpoint(cls):
        return {
            "core_slice": "ritsuko_magi_observation_v1",
            "selected_capability": "pkb_search",
            "magi_session": cls.review_session(),
        }

    def test_create_task_uses_state_driven_core_slice_and_read_only_scope(self):
        db = _DB()
        create_task(
            db,
            task_id=TASK_ID,
            request="メインPCのGPUの種類は？",
            member_specs=[{"name": "MELCHIOR", "enabled": True}],
        )
        task_call = next(
            call for call in db.cur.calls
            if "INSERT INTO secretary.tasks" in call[0]
        )
        self.assertEqual(task_call[1][0], TASK_ID)
        checkpoint = task_call[1][-1].obj
        scope = task_call[1][-2].obj
        self.assertEqual(checkpoint["core_slice"], "ritsuko_magi_observation_v1")
        self.assertTrue(scope["pkb_read"])
        self.assertTrue(scope["web_research"])
        self.assertTrue(scope["finance_read"])
        self.assertFalse(scope["files_read"])
        self.assertFalse(scope["external_actions"])
        self.assertFalse(scope["cloud_private_context"])

    def test_pending_start_reserves_intent_and_claims_once(self):
        """Taskをpendingとして保存し、同一Intentのclaimのみを許可する。"""
        members = [{"name": "MELCHIOR", "enabled": True}]
        policy = {"profile_id": "test-profile", "revision": 1, "grants": []}
        db = _DB(fetches=[(TASK_ID,)])
        reserve_start(
            db, task_id=TASK_ID, request="現在の情報を教えて",
            member_specs=members, decision_strategy="llm",
            policy_snapshot=policy,
        )
        ins = next(call for call in db.cur.calls
                   if "INSERT INTO secretary.tasks" in call[0])
        self.assertIn("'pending'", ins[0])
        checkpoint = ins[1][-1].obj
        self.assertEqual(checkpoint["start_intent"]["decision_strategy"], "llm")
        self.assertEqual(checkpoint["start_intent"]["policy_snapshot"], policy)
        self.assertEqual(checkpoint["member_specs"], members)
        self.assertIsNone(checkpoint["magi_session"])
        self.assertTrue(any("task_accepted" in sql for sql, _ in db.cur.calls))

        # Same UUID and goal after a lost HTTP response is a replay, not a second Task.
        replay = _DB(fetches=[None, (
            "現在の情報を教えて", "local_user", checkpoint,
        )])
        self.assertFalse(reserve_start(
            replay, task_id=TASK_ID, request="現在の情報を教えて",
            member_specs=members, decision_strategy="llm",
            policy_snapshot=policy,
        ))
        self.assertFalse(any("task_accepted" in sql for sql, _ in replay.cur.calls))

        conflict = _DB(fetches=[None, (
            "現在の情報を教えて", "local_user", checkpoint,
        )])
        with self.assertRaisesRegex(ValueError, "task_id_intent_mismatch"):
            reserve_start(
                conflict, task_id=TASK_ID, request="別の依頼",
                member_specs=members, decision_strategy="llm",
                policy_snapshot=policy,
            )


        db2 = _DB(fetches=[(
            "現在の情報を教えて", "pending", checkpoint,
        )])
        claim_start(
            db2, task_id=TASK_ID, request="現在の情報を教えて",
            member_specs=members,
        )
        update = next(call for call in db2.cur.calls
                      if "UPDATE secretary.tasks" in call[0])
        self.assertIn("status='running'", update[0])
        self.assertEqual(update[1][0].obj["start_intent"]["state"], "claimed")
        self.assertIn("FOR UPDATE", db2.cur.calls[0][0])

        db3 = _DB(fetches=[(
            "現在の情報を教えて", "running", checkpoint,
        )])
        with self.assertRaisesRegex(ValueError, "task_start_not_pending"):
            claim_start(db3, task_id=TASK_ID, request="現在の情報を教えて",
                        member_specs=members)
        self.assertFalse(any("UPDATE secretary.tasks" in sql for sql, _ in db3.cur.calls))

    def test_pending_start_rejects_mismatched_intent_and_reads_only_pending(self):
        """受理時のメンバー設定と本文を書換えたclaimは成功させない。"""
        checkpoint = {
            "core_slice": "ritsuko_magi_observation_v1",
            "member_specs": [{"name": "MELCHIOR", "enabled": True}],
            "start_intent": {
                "state": "pending", "decision_strategy": "standard",
                "policy_snapshot": None,
            },
        }
        db = _DB(fetches=[("本来の依頼", "pending", checkpoint)])
        with self.assertRaisesRegex(ValueError, "pending_task_intent_mismatch"):
            claim_start(
                db, task_id=TASK_ID, request="別の依頼",
                member_specs=checkpoint["member_specs"],
            )
        self.assertFalse(any("UPDATE secretary.tasks" in sql for sql, _ in db.cur.calls))

        pending = _DB(fetches=[(TASK_ID, "本来の依頼", checkpoint)])
        rows = list_pending_starts(pending, limit=1)
        self.assertEqual(rows[0]["task_id"], TASK_ID)
        self.assertEqual(rows[0]["decision_strategy"], "standard")
        self.assertEqual(rows[0]["request"], "本来の依頼")
        self.assertIn("status='pending'", pending.cur.calls[0][0])
        self.assertEqual(rows[0]["member_specs"], checkpoint["member_specs"])

    def test_stop_before_claim_pauses_task_and_marks_running_as_request_only(self):
        """未着手の停止だけを確定し、実行中は要求と停止完了を混同しない。"""
        db = _DB(fetches=[("pending", {"phase": "accepted"})])
        self.assertEqual(request_stop(db, task_id=TASK_ID), "paused")
        update = next(call for call in db.cur.calls if "UPDATE secretary.tasks" in call[0])
        self.assertEqual(update[1][0], "paused")
        self.assertTrue(update[1][1].obj["stop_requested"])
        self.assertEqual(update[1][1].obj["final_core_decision"]["task_status"], "paused")

        running = _DB(fetches=[("running", {"phase": "observe"})])
        self.assertEqual(request_stop(running, task_id=TASK_ID), "running")
        update2 = next(call for call in running.cur.calls if "UPDATE secretary.tasks" in call[0])
        self.assertEqual(update2[1][0], "running")
        self.assertTrue(update2[1][1].obj["stop_requested"])
        self.assertNotIn("final_core_decision", update2[1][1].obj)
        blocked = _DB(fetches=[("completed", {"phase": "completed"})])
        with self.assertRaisesRegex(ValueError, "task_not_stoppable"):
            request_stop(blocked, task_id=TASK_ID)
        self.assertFalse(any("UPDATE secretary.tasks" in sql for sql, _ in blocked.cur.calls))

    def test_claim_user_resume_requires_waiting_magi_task_and_preserves_snapshot(self):
        saved_session = {
            "task_id": str(TASK_ID),
            "status": "waiting_user",
            "pending_requests": [{"request_id": "REQ-1", "source": "user"}],
        }
        db = _DB(fetches=[(
            "waiting_external",
            {
                "core_slice": "ritsuko_magi_observation_v1",
                "selected_capability": "pkb_search",
                "magi_session": saved_session,
            },
        )])
        session, capability = claim_user_resume(
            db,
            task_id=TASK_ID,
            reply_length=18,
            reply_fingerprint="a" * 64,
        )
        self.assertEqual(session, saved_session)
        self.assertEqual(capability, "pkb_search")
        sql = "\n".join(call[0] for call in db.cur.calls)
        self.assertIn("SET status='running'", sql)
        self.assertIn("core.magi.user_reply_received", sql)

    def test_running_user_resume_can_retry_only_same_reply(self):
        saved_session = {
            "task_id": str(TASK_ID),
            "status": "waiting_user",
            "pending_requests": [{"request_id": "REQ-1", "source": "user"}],
        }
        checkpoint = {
            "core_slice": "ritsuko_magi_observation_v1",
            "selected_capability": "pkb_search",
            "magi_session": saved_session,
            "user_resume": {
                "status": "processing",
                "reply_length": 18,
                "reply_fingerprint": "b" * 64,
                "pending_request_ids": ["REQ-1"],
            },
        }
        db = _DB(fetches=[("running", checkpoint)])
        session, capability = claim_user_resume(
            db,
            task_id=TASK_ID,
            reply_length=18,
            reply_fingerprint="b" * 64,
        )
        self.assertEqual(session, saved_session)
        self.assertEqual(capability, "pkb_search")
        sql = "\n".join(call[0] for call in db.cur.calls)
        self.assertIn("core.magi.user_reply_resumed", sql)
        self.assertNotIn("SET status='running'", sql)

        mismatch_db = _DB(fetches=[("running", checkpoint)])
        with self.assertRaisesRegex(ValueError, "user_resume_reply_mismatch"):
            claim_user_resume(
                mismatch_db,
                task_id=TASK_ID,
                reply_length=12,
                reply_fingerprint="c" * 64,
            )

    def test_abort_user_resume_returns_retryable_waiting_state(self):
        checkpoint = {
            "core_slice": "ritsuko_magi_observation_v1",
            "magi_session": {
                "status": "waiting_user",
                "user_question": "GPUモデルを教えてください",
            },
            "user_resume": {
                "status": "processing",
                "reply_length": 18,
                "reply_fingerprint": "d" * 64,
                "pending_request_ids": ["REQ-1"],
            },
        }
        db = _DB(fetches=[("running", checkpoint)])
        abort_user_resume(
            db,
            task_id=TASK_ID,
            error="AsyncRequestTimeout",
        )
        update = next(
            call for call in db.cur.calls
            if "SET status='waiting_external'" in call[0]
        )
        saved = update[1][0].obj
        self.assertEqual(saved["phase"], "awaiting_clarification")
        self.assertEqual(saved["user_resume"]["status"], "retry_required")
        self.assertEqual(
            saved["final_core_decision"]["next_step"],
            "retry_user_resume",
        )

    def test_persist_verified_candidate_completes_task(self):
        db = _DB(fetches=[({"core_slice": "ritsuko_magi_observation_v1"},)])
        projection = persist_session(
            db,
            task_id=TASK_ID,
            selected_capability="pkb_search",
            session={
                "status": "candidate_ready",
                "next_step": "review_answer_candidate",
                "tool_read_executed": True,
                "observations": [
                    {"source": "pkb", "verified": True, "text": "verified"}
                ],
                "detail": {
                    "answer_candidate": "メインPCのGPUは Radeon RX 9070 XT です."
                },
                "turns": [{}, {}, {}],
            },
        )
        self.assertEqual(projection["task_status"], "completed")

    def test_prepare_memory_intake_is_task_linked_and_retry_safe(self):
        initial = self.review_checkpoint()
        db = _DB(fetches=[("waiting_external", initial)])
        intake = prepare_memory_intake(db, task_id=TASK_ID)
        self.assertEqual(
            intake.raw_text,
            "メインPCのGPUモデル名: Radeon RX 9070 XT",
        )
        self.assertIn(str(TASK_ID), intake.source_ref)
        prepared_update = next(
            call for call in db.cur.calls
            if "UPDATE secretary.tasks SET checkpoint" in call[0]
        )
        prepared = prepared_update[1][0].obj["proposal_memory_intake"]
        replay_db = _DB(fetches=[(
            "waiting_external",
            {**initial, "proposal_memory_intake": prepared},
        )])
        replay = prepare_memory_intake(replay_db, task_id=TASK_ID)
        self.assertEqual(replay.input_id, intake.input_id)

    def test_claim_answer_only_builds_verified_review_observation(self):
        db = _DB(fetches=[("waiting_external", self.review_checkpoint())])
        session, capability, observation, review = claim_proposal_review(
            db,
            task_id=TASK_ID,
            decision="answer_only",
        )
        self.assertEqual(session["status"], "proposal_ready")
        self.assertEqual(capability, "pkb_search")
        self.assertEqual(review["status"], "processing")
        self.assertEqual(review["decision"], "answer_only")
        self.assertEqual(observation["source"], "proposal_review")
        self.assertTrue(observation["verified"])
        self.assertIsNone(observation["memory_intake"])
        update = next(
            call for call in db.cur.calls
            if "SET status='running'" in call[0]
        )
        self.assertEqual(
            update[1][0].obj["proposal_review"]["status"],
            "processing",
        )

    def test_claim_answer_only_uses_grounded_knowledge_when_answer_missing(self):
        checkpoint = self.review_checkpoint()
        checkpoint["magi_session"]["detail"]["answer_candidate"] = None
        db = _DB(fetches=[("waiting_external", checkpoint)])
        _session, _capability, observation, review = claim_proposal_review(
            db,
            task_id=TASK_ID,
            decision="answer_only",
        )
        self.assertEqual(
            review["answer"],
            "メインPCのGPUモデル名: Radeon RX 9070 XT",
        )
        self.assertEqual(
            review["answer_source"],
            "knowledge_candidate_fallback",
        )
        self.assertEqual(observation["answer"], review["answer"])
        self.assertEqual(
            observation["answer_source"],
            "knowledge_candidate_fallback",
        )

    def test_prepare_memory_intake_still_uses_knowledge_when_answer_missing(self):
        checkpoint = self.review_checkpoint()
        checkpoint["magi_session"]["detail"]["answer_candidate"] = None
        db = _DB(fetches=[("waiting_external", checkpoint)])
        intake = prepare_memory_intake(db, task_id=TASK_ID)
        self.assertEqual(
            intake.raw_text,
            "メインPCのGPUモデル名: Radeon RX 9070 XT",
        )

    def test_answer_only_refused_after_memory_intake_prepared(self):
        checkpoint = self.review_checkpoint()
        checkpoint["proposal_memory_intake"] = {"input_id": "already-prepared"}
        db = _DB(fetches=[("waiting_external", checkpoint)])
        with self.assertRaisesRegex(ValueError, "memory_review_already_prepared"):
            claim_proposal_review(
                db,
                task_id=TASK_ID,
                decision="answer_only",
            )

    def test_claim_memory_review_returns_memory_observation_with_pending_receipt(self):
        checkpoint = self.review_checkpoint()
        checkpoint["proposal_memory_intake"] = {
            "input_id": "input-1",
            "raw_text": "メインPCのGPUモデル名: Radeon RX 9070 XT",
        }
        db = _DB(fetches=[("waiting_external", checkpoint)])
        memory_result = {
            "status": "committed",
            "input_id": "input-1",
            "source_id": str(SOURCE_ID),
            "candidates": [{
                "candidate_id": "candidate-1",
                "decision": "pending",
                "reason": "unresolved",
                "claim_id": None,
                "pending_id": "pending-1",
                "derived_claim_ids": [],
            }],
        }
        _session, _capability, observation, review = claim_proposal_review(
            db,
            task_id=TASK_ID,
            decision="remember",
            memory_result=memory_result,
        )
        self.assertEqual(observation["source"], "memory_intake")
        self.assertTrue(observation["verified"])
        self.assertIn("pending", observation["text"])
        self.assertEqual(
            review["memory_intake"]["receipts"][0]["decision"],
            "pending",
        )

    def test_processing_review_can_resume_same_decision_after_restart(self):
        checkpoint = self.review_checkpoint()
        checkpoint.update({
            "proposal_memory_intake": {
                "input_id": "input-1",
                "raw_text": "メインPCのGPUモデル名: Radeon RX 9070 XT",
            },
            "proposal_review": {
                "decision": "remember",
                "status": "processing",
                "answer": "メインPCのGPUはRadeon RX 9070 XTです。",
                "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
                "user_text": "Radeon RX 9070 XT",
                "responds_to": ["REQ-1"],
                "memory_intake": {
                    "input_id": "input-1",
                    "status": "committed",
                    "source_id": str(SOURCE_ID),
                    "receipts": [{"decision": "pending"}],
                },
            },
        })
        db = _DB(fetches=[("running", checkpoint)])
        _session, capability, observation, review = claim_proposal_review(
            db,
            task_id=TASK_ID,
            decision="remember",
            memory_result={
                "status": "replayed",
                "input_id": "input-1",
                "source_id": str(SOURCE_ID),
                "candidates": [{"decision": "pending"}],
            },
        )
        self.assertEqual(capability, "pkb_search")
        self.assertEqual(review["status"], "processing")
        self.assertEqual(observation["source"], "memory_intake")
        sql = "\n".join(call[0] for call in db.cur.calls)
        self.assertIn("core.magi.proposal_review_resumed", sql)
        self.assertNotIn("SET status='running'", sql)

    def test_processing_review_rejects_switching_decision(self):
        checkpoint = self.review_checkpoint()
        checkpoint["proposal_review"] = {
            "decision": "answer_only",
            "status": "processing",
            "answer": "メインPCのGPUはRadeon RX 9070 XTです。",
            "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
            "user_text": "Radeon RX 9070 XT",
            "responds_to": ["REQ-1"],
            "memory_intake": None,
        }
        db = _DB(fetches=[("running", checkpoint)])
        with self.assertRaisesRegex(
            ValueError,
            "proposal_review_already_processing",
        ):
            claim_proposal_review(
                db,
                task_id=TASK_ID,
                decision="remember",
                memory_result={
                    "status": "committed",
                    "input_id": "input-1",
                },
            )

    def test_finalize_answer_only_uses_fallback_answer_end_to_end(self):
        checkpoint = self.review_checkpoint()
        checkpoint["magi_session"]["detail"]["answer_candidate"] = None
        claim_db = _DB(fetches=[("waiting_external", checkpoint)])
        session, capability, observation, review = claim_proposal_review(
            claim_db,
            task_id=TASK_ID,
            decision="answer_only",
        )
        self.assertEqual(
            review["answer"],
            "メインPCのGPUモデル名: Radeon RX 9070 XT",
        )

        processing_checkpoint = self.review_checkpoint()
        processing_checkpoint["magi_session"]["detail"]["answer_candidate"] = None
        processing_checkpoint["proposal_review"] = review
        evaluation = {
            "state": "READY",
            "reason": "本人回答だけで元質問へ回答可能",
            "answer_candidate": review["answer"],
        }
        reviewed_session = dict(session)
        reviewed_session.update({
            "status": "review_evaluated",
            "next_step": "ritsuko_finalize_review",
            "post_review_evaluation": evaluation,
            "observations": [
                *session["observations"],
                observation,
            ],
            "turns": [
                *session["turns"],
                {
                    "question_purpose": "evaluate_review_result",
                    "status": "ok",
                    "request_envelope": {
                        "task_id": str(TASK_ID),
                        "question_purpose": "evaluate_review_result",
                    },
                    "response": evaluation,
                },
            ],
        })
        finalize_db = _DB(fetches=[("running", processing_checkpoint)])
        completed = finalize_proposal_review(
            finalize_db,
            task_id=TASK_ID,
            session=reviewed_session,
            selected_capability=capability,
        )
        self.assertEqual(completed["status"], "completed")
        update = next(
            call for call in finalize_db.cur.calls
            if "SET status='completed'" in call[0]
        )
        saved = update[1][0].obj
        self.assertEqual(
            saved["message"],
            "メインPCのGPUモデル名: Radeon RX 9070 XT",
        )
        self.assertEqual(
            saved["final_core_decision"]["reason"],
            "proposal_review_evaluated_answer_only",
        )

    def test_finalize_review_requires_ok_review_turn_and_completes_task(self):
        checkpoint = self.review_checkpoint()
        checkpoint["proposal_review"] = {
            "decision": "remember",
            "status": "processing",
            "answer": "メインPCのGPUはRadeon RX 9070 XTです。",
            "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
            "user_text": "Radeon RX 9070 XT",
            "responds_to": ["REQ-1"],
            "memory_intake": {
                "input_id": "input-1",
                "status": "committed",
                "source_id": str(SOURCE_ID),
                "receipts": [{"decision": "pending"}],
            },
        }
        session = self.review_session()
        session.update({
            "status": "review_evaluated",
            "next_step": "ritsuko_finalize_review",
            "post_review_evaluation": {
                "state": "READY",
                "reason": "本人回答で元質問には回答可能",
                "answer_candidate": "メインPCのGPUはRadeon RX 9070 XTです。",
            },
            "observations": [
                *session["observations"],
                {
                    "source": "memory_intake",
                    "verified": True,
                    "confidentiality": "private",
                    "review_decision": "remember",
                    "answer": "メインPCのGPUはRadeon RX 9070 XTです。",
                    "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
                    "user_text": "Radeon RX 9070 XT",
                    "responds_to": ["REQ-1"],
                    "text": "Memory Intake committed / pending",
                    "memory_intake": {
                        "input_id": "input-1",
                        "status": "committed",
                        "source_id": str(SOURCE_ID),
                        "receipts": [{"decision": "pending"}],
                    },
                },
            ],
            "turns": [
                *session["turns"],
                {
                    "question_purpose": "evaluate_review_result",
                    "status": "ok",
                    "request_envelope": {
                        "task_id": str(TASK_ID),
                        "question_purpose": "evaluate_review_result",
                    },
                    "response": {
                        "state": "READY",
                        "reason": "本人回答で元質問には回答可能",
                        "answer_candidate": "メインPCのGPUはRadeon RX 9070 XTです。",
                    },
                },
            ],
        })
        db = _DB(fetches=[("running", checkpoint)])
        review = finalize_proposal_review(
            db,
            task_id=TASK_ID,
            session=session,
            selected_capability="pkb_search",
        )
        self.assertEqual(review["status"], "completed")
        self.assertEqual(review["magi_evaluation"]["state"], "READY")
        update = next(
            call for call in db.cur.calls
            if "SET status='completed'" in call[0]
        )
        saved = update[1][0].obj
        self.assertEqual(saved["message"], review["answer"])
        self.assertEqual(saved["magi_session"]["status"], "review_evaluated")
        self.assertEqual(
            saved["final_core_decision"]["reason"],
            "proposal_review_evaluated_memory",
        )

    def test_finalize_review_rejects_nonfinalizable_magi_state(self):
        checkpoint = self.review_checkpoint()
        checkpoint["proposal_review"] = {
            "decision": "answer_only",
            "status": "processing",
            "answer": "メインPCのGPUはRadeon RX 9070 XTです。",
            "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
            "user_text": "Radeon RX 9070 XT",
            "responds_to": ["REQ-1"],
            "memory_intake": None,
        }
        session = self.review_session()
        session.update({
            "status": "review_evaluated",
            "next_step": "ritsuko_finalize_review",
            "post_review_evaluation": {
                "state": "NEED_INFORMATION",
                "reason": "unexpected extra lookup",
            },
            "observations": [
                *session["observations"],
                {
                    "source": "proposal_review",
                    "verified": True,
                    "confidentiality": "private",
                    "review_decision": "answer_only",
                    "answer": "メインPCのGPUはRadeon RX 9070 XTです。",
                    "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
                    "user_text": "Radeon RX 9070 XT",
                    "responds_to": ["REQ-1"],
                    "text": "answer only",
                    "memory_intake": None,
                },
            ],
            "turns": [
                *session["turns"],
                {
                    "question_purpose": "evaluate_review_result",
                    "status": "ok",
                    "request_envelope": {
                        "task_id": str(TASK_ID),
                        "question_purpose": "evaluate_review_result",
                    },
                    "response": {
                        "state": "NEED_INFORMATION",
                        "reason": "unexpected extra lookup",
                    },
                },
            ],
        })
        db = _DB(fetches=[("running", checkpoint)])
        with self.assertRaisesRegex(
            ValueError,
            "proposal_review_evaluation_not_finalizable",
        ):
            finalize_proposal_review(
                db,
                task_id=TASK_ID,
                session=session,
            )

    def test_abort_review_returns_task_to_retryable_awaiting_review(self):
        checkpoint = self.review_checkpoint()
        checkpoint["proposal_review"] = {
            "decision": "answer_only",
            "status": "processing",
            "answer": "メインPCのGPUはRadeon RX 9070 XTです。",
        }
        db = _DB(fetches=[("running", checkpoint)])
        abort_proposal_review(
            db,
            task_id=TASK_ID,
            error="AsyncRequestTimeout",
        )
        update = next(
            call for call in db.cur.calls
            if "SET status='waiting_external'" in call[0]
        )
        saved = update[1][0].obj
        self.assertEqual(saved["phase"], "awaiting_review")
        self.assertEqual(saved["proposal_review"]["status"], "retry_required")
        self.assertEqual(
            saved["final_core_decision"]["next_step"],
            "retry_proposal_review",
        )

    def test_record_pkb_read_writes_source_action_result_and_checkpoint(self):
        db = _DB(fetches=[
            None,
            (SOURCE_ID,),
            (ACTION_ID,),
            (RESULT_ID,),
            ({"core_slice": "ritsuko_magi_observation_v1"},),
        ])
        action_id, result_id = record_pkb_read(
            db,
            task_id=TASK_ID,
            pending_request={
                "request_ids": ["REQ-1"],
                "what": "メインPCのGPUモデル",
            },
            execution={
                "tool": "pkb",
                "operation": "entity_detail",
                "citation": "fixture",
                "verified_by": "deterministic_pkb_query",
                "total": 1,
                "answer": "Radeon RX 9070 XT",
                "result": {
                    "result_kind": "entity_detail",
                    "current": [{"predicate": "model", "value": "Radeon RX 9070 XT"}],
                },
            },
        )
        self.assertEqual(action_id, str(ACTION_ID))
        self.assertEqual(result_id, str(RESULT_ID))
        sql = "\n".join(call[0] for call in db.cur.calls)
        self.assertIn("INSERT INTO secretary.sources", sql)
        self.assertIn("INSERT INTO secretary.actions", sql)
        self.assertIn("INSERT INTO secretary.results", sql)
        self.assertIn("core.magi.source_observed", sql)

    def test_record_web_read_uses_public_source_and_generic_audit(self):
        db = _DB(fetches=[
            None,
            (SOURCE_ID,),
            (ACTION_ID,),
            (RESULT_ID,),
            ({"core_slice": "ritsuko_magi_observation_v1"},),
        ])
        action_id, result_id = record_source_read(
            db,
            task_id=TASK_ID,
            pending_request={
                "source": "web",
                "request_ids": ["REQ-W"],
                "what": "official driver",
            },
            execution={
                "status": "ok",
                "source": "web",
                "capability": "web_research",
                "confidentiality": "public",
                "tool": "web",
                "operation": "research",
                "citation": "fixture web",
                "verified_by": "bounded_web_retrieval",
                "total": 1,
                "answer": "official result",
                "result": {
                    "result_kind": "web_research",
                    "hits": [{"title": "official"}],
                },
            },
        )
        self.assertEqual(action_id, str(ACTION_ID))
        self.assertEqual(result_id, str(RESULT_ID))
        source_call = next(
            call for call in db.cur.calls
            if "INSERT INTO secretary.sources" in call[0]
        )
        self.assertEqual(source_call[1][2], "public")
        audit_sql = "\n".join(call[0] for call in db.cur.calls)
        self.assertIn("core.magi.source_observed", audit_sql)


if __name__ == "__main__":
    unittest.main()
