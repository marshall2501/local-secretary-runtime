from __future__ import annotations

import unittest

from ritsuko.core.magi_bridge import (
    pending_pkb_request,
    proposal_review_observation,
    reviewable_user_knowledge_proposal,
    task_projection,
    verified_pkb_observation,
)


class MagiCoreBridgeTests(unittest.TestCase):
    def test_only_all_pkb_pending_requests_are_auto_read_eligible(self):
        session = {
            "status": "waiting_information",
            "pending_requests": [{
                "request_id": "REQ-1",
                "source": "pkb",
                "what": "メインPCのGPUモデル",
                "reason": "回答に必要",
            }],
        }
        request = pending_pkb_request(session)
        self.assertEqual(request["source"], "pkb")
        self.assertEqual(request["request_ids"], ["REQ-1"])
        session["pending_requests"].append({
            "request_id": "REQ-2",
            "source": "web",
            "what": "最新公開情報",
            "reason": "鮮度確認",
        })
        self.assertIsNone(pending_pkb_request(session))

    def test_verified_observation_is_private_bounded_and_linked(self):
        observation = verified_pkb_observation(
            {
                "total": 1,
                "answer": "PKBの記録では Radeon RX 9070 XT。",
                "result": {
                    "result_kind": "entity_detail",
                    "current": [{
                        "predicate": "model",
                        "value": "Radeon RX 9070 XT",
                    }],
                },
            },
            {"request_ids": ["REQ-1"]},
        )
        self.assertTrue(observation["verified"])
        self.assertEqual(observation["source"], "pkb")
        self.assertEqual(observation["confidentiality"], "private")
        self.assertEqual(observation["responds_to"], ["REQ-1"])

    @staticmethod
    def proposal_session():
        return {
            "status": "proposal_ready",
            "tool_read_executed": True,
            "detail": {
                "state": "KNOWLEDGE_CANDIDATE",
                "answer_candidate": "メインPCのGPUはRadeon RX 9070 XTです。",
                "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
            },
            "observations": [
                {"source": "pkb", "verified": True, "text": "model unavailable"},
                {
                    "source": "user_clarification",
                    "verified": False,
                    "text": "Radeon RX 9070 XT",
                    "responds_to": ["REQ-1"],
                },
            ],
        }

    def test_user_grounded_knowledge_proposal_is_reviewable(self):
        review = reviewable_user_knowledge_proposal(self.proposal_session())
        self.assertIsNotNone(review)
        self.assertEqual(review["user_text"], "Radeon RX 9070 XT")
        self.assertEqual(review["responds_to"], ["REQ-1"])

    def test_missing_answer_candidate_falls_back_to_grounded_knowledge(self):
        session = self.proposal_session()
        session["detail"]["answer_candidate"] = None
        review = reviewable_user_knowledge_proposal(session)
        self.assertIsNotNone(review)
        self.assertEqual(
            review["answer"],
            "メインPCのGPUモデル名: Radeon RX 9070 XT",
        )
        self.assertEqual(
            review["answer_source"],
            "knowledge_candidate_fallback",
        )

    def test_model_only_or_missing_user_proposal_is_not_reviewable(self):
        session = self.proposal_session()
        session["detail"]["answer_candidate"] = "メインPCのGPUは別モデルです。"
        session["detail"]["knowledge_candidate"] = "メインPCのGPUモデル名: 別モデル"
        self.assertIsNone(reviewable_user_knowledge_proposal(session))
        session = self.proposal_session()
        session["detail"]["answer_candidate"] = None
        session["detail"]["knowledge_candidate"] = "メインPCのGPUモデル名: 別モデル"
        self.assertIsNone(reviewable_user_knowledge_proposal(session))
        session = self.proposal_session()
        session["observations"] = session["observations"][:1]
        self.assertIsNone(reviewable_user_knowledge_proposal(session))

    def test_answer_only_review_observation_is_verified_private(self):
        proposal = reviewable_user_knowledge_proposal(self.proposal_session())
        observation = proposal_review_observation(
            proposal,
            decision="answer_only",
        )
        self.assertEqual(observation["source"], "proposal_review")
        self.assertTrue(observation["verified"])
        self.assertEqual(observation["confidentiality"], "private")
        self.assertEqual(observation["review_decision"], "answer_only")
        self.assertEqual(observation["answer_source"], "answer_candidate")
        self.assertIsNone(observation["memory_intake"])

    def test_memory_review_observation_preserves_pending_semantics(self):
        proposal = reviewable_user_knowledge_proposal(self.proposal_session())
        observation = proposal_review_observation(
            proposal,
            decision="remember",
            memory_summary={
                "input_id": "input-1",
                "status": "committed",
                "source_id": "source-1",
                "receipts": [{
                    "candidate_id": "candidate-1",
                    "decision": "pending",
                    "reason": "unresolved",
                    "claim_id": None,
                    "pending_id": "pending-1",
                    "derived_claim_ids": [],
                }],
            },
        )
        self.assertEqual(observation["source"], "memory_intake")
        self.assertIn("pending", observation["text"])
        self.assertIn("確定PKB current factではない", observation["text"])
        self.assertEqual(
            observation["memory_intake"]["receipts"][0]["decision"],
            "pending",
        )

    def test_candidate_ready_projects_to_completed_task(self):
        projection = task_projection({
            "status": "candidate_ready",
            "next_step": "review_answer_candidate",
            "tool_read_executed": True,
            "observations": [
                {"source": "pkb", "verified": True, "text": "verified", "result_id": "result-pkb-1"}
            ],
            "detail": {
                "answer_candidate": "メインPCのGPUは Radeon RX 9070 XT です。"
            },
        })
        self.assertEqual(projection["task_status"], "completed")
        self.assertEqual(projection["next_step"], "respond")

    def test_web_or_finance_verified_observation_can_ground_completion(self):
        for source in ("web", "finance"):
            with self.subTest(source=source):
                projection = task_projection({
                    "status": "candidate_ready",
                    "next_step": "review_answer_candidate",
                    "tool_read_executed": True,
                    "pending_requests": [],
                    "observations": [{
                        "source": source,
                        "verified": True,
                        "confidentiality": (
                            "public" if source == "web" else "private"
                        ),
                        "text": "verified",
                        "result_id": "result-" + source + "-1",
                    }],
                    "detail": {"answer_candidate": "grounded answer"},
                })
                self.assertEqual(projection["task_status"], "completed")

    def test_unresolved_request_prevents_completion_even_with_verified_read(self):
        projection = task_projection({
            "status": "candidate_ready",
            "tool_read_executed": True,
            "pending_requests": [{
                "request_id": "REQ-2",
                "source": "web",
                "what": "still blocking",
            }],
            "observations": [{
                "source": "pkb",
                "verified": True,
                "confidentiality": "private",
                "text": "partial",
            }],
            "detail": {"answer_candidate": "premature"},
        })
        self.assertEqual(projection["task_status"], "waiting_external")

    def test_unverified_answer_candidate_does_not_complete_task(self):
        projection = task_projection({
            "status": "candidate_ready",
            "next_step": "review_answer_candidate",
            "tool_read_executed": False,
            "observations": [
                {"source": "manual_test_input", "verified": False, "text": "guess"}
            ],
            "detail": {"answer_candidate": "GPUは何かです。"},
        })
        self.assertEqual(projection["task_status"], "waiting_external")
        self.assertEqual(projection["next_step"], "review_answer_candidate")

    def test_candidate_requires_real_result_id_not_merely_verified_flag(self):
        """標準方式でも観測文字列だけでTaskを完了しない。"""
        result = task_projection({
            "status": "candidate_ready",
            "tool_read_executed": True,
            "observations": [{"source": "web", "verified": True, "text": "not persisted"}],
            "detail": {"answer_candidate": "検証済みと自己申告"},
        })
        self.assertEqual(result["task_status"], "waiting_external")

    def test_goal_claim_uses_same_projector_regardless_of_strategy_label(self):
        """保存済みGoal提案がある場合、方式ラベルではなく根拠を照合する。"""
        session = {
            "status": "candidate_ready",
            "decision_strategy": "standard",
            "goal_judgment": {
                "next": "respond", "fulfillment": "met", "answer": "説明",
                "evidence": {"basis": "not_required", "result_ids": []},
            },
            "detail": {"answer_candidate": "別回答"},
        }
        result = task_projection(session)
        self.assertEqual(result["task_status"], "completed")
        self.assertEqual(result["message"], "説明")
        # LLMセッションからclaimが失われても、標準方式のfallbackで
        # 存在しないGoal充足をでっち上げない。
        session["decision_strategy"] = "llm"
        session.pop("goal_judgment")
        self.assertEqual(task_projection(session)["task_status"], "failed")

    def test_user_stop_projects_to_paused_not_failed(self):
        projection = task_projection({
            "status": "stopped",
            "next_step": "user_requested_stop",
            "detail": {},
        })
        self.assertEqual(projection["task_status"], "paused")
        self.assertEqual(projection["phase"], "paused")


if __name__ == "__main__":
    unittest.main()
