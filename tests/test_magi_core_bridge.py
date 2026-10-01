from __future__ import annotations

import unittest

from pkb_proto.magi_core_bridge import (
    pending_pkb_request,
    reviewable_user_knowledge_proposal,
    task_projection,
    verified_pkb_observation,
)


class MagiCoreBridgeTests(unittest.TestCase):
    def test_only_all_pkb_pending_requests_are_auto_read_eligible(self):
        session = {
            "status": "waiting_information",
            "pending_requests": [
                {
                    "request_id": "REQ-1",
                    "source": "pkb",
                    "what": "メインPCのGPUモデル",
                    "reason": "回答に必要",
                }
            ],
        }
        request = pending_pkb_request(session)
        self.assertEqual(request["source"], "pkb")
        self.assertEqual(request["request_ids"], ["REQ-1"])
        self.assertIn("GPU", request["what"])

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
                    "current": [
                        {"predicate": "model", "value": "Radeon RX 9070 XT"}
                    ],
                },
            },
            {"request_ids": ["REQ-1"]},
        )
        self.assertTrue(observation["verified"])
        self.assertEqual(observation["source"], "pkb")
        self.assertEqual(observation["confidentiality"], "private")
        self.assertEqual(observation["responds_to"], ["REQ-1"])
        self.assertEqual(observation["result_count"], 1)

    def test_candidate_ready_projects_to_completed_task(self):
        projection = task_projection({
            "status": "candidate_ready",
            "next_step": "review_answer_candidate",
            "tool_read_executed": True,
            "observations": [
                {"source": "pkb", "verified": True, "text": "verified"}
            ],
            "detail": {
                "answer_candidate": "メインPCのGPUは Radeon RX 9070 XT です。"
            },
        })
        self.assertEqual(projection["task_status"], "completed")
        self.assertEqual(projection["next_step"], "respond")
        self.assertIn("Radeon RX 9070 XT", projection["message"])

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
        self.assertEqual(
            projection["reason"],
            "answer_candidate_not_grounded_by_verified_pkb_observation",
        )

    def test_user_grounded_knowledge_proposal_is_reviewable(self):
        session = {
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
        review = reviewable_user_knowledge_proposal(session)
        self.assertIsNotNone(review)
        self.assertEqual(review["user_text"], "Radeon RX 9070 XT")
        self.assertEqual(review["responds_to"], ["REQ-1"])

    def test_model_only_or_paraphrased_proposal_is_not_reviewable(self):
        base = {
            "status": "proposal_ready",
            "tool_read_executed": True,
            "detail": {
                "state": "KNOWLEDGE_CANDIDATE",
                "answer_candidate": "メインPCのGPUは別モデルです。",
                "knowledge_candidate": "メインPCのGPUモデル名: 別モデル",
            },
            "observations": [
                {"source": "pkb", "verified": True, "text": "model unavailable"},
                {
                    "source": "user_clarification",
                    "text": "Radeon RX 9070 XT",
                    "responds_to": ["REQ-1"],
                },
            ],
        }
        self.assertIsNone(reviewable_user_knowledge_proposal(base))
        base["observations"] = base["observations"][:1]
        self.assertIsNone(reviewable_user_knowledge_proposal(base))

    def test_user_grounded_knowledge_proposal_is_reviewable(self):
        session = {
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
                    "text": "Radeon RX 9070 XT",
                    "responds_to": ["REQ-1"],
                },
            ],
        }
        review = reviewable_user_knowledge_proposal(session)
        self.assertIsNotNone(review)
        self.assertEqual(review["user_text"], "Radeon RX 9070 XT")
        self.assertEqual(review["responds_to"], ["REQ-1"])

    def test_model_only_or_paraphrased_proposal_is_not_reviewable(self):
        session = {
            "status": "proposal_ready",
            "tool_read_executed": True,
            "detail": {
                "state": "KNOWLEDGE_CANDIDATE",
                "answer_candidate": "メインPCのGPUは別モデルです。",
                "knowledge_candidate": "メインPCのGPUモデル名: 別モデル",
            },
            "observations": [
                {"source": "pkb", "verified": True, "text": "model unavailable"},
                {
                    "source": "user_clarification",
                    "text": "Radeon RX 9070 XT",
                    "responds_to": ["REQ-1"],
                },
            ],
        }
        self.assertIsNone(reviewable_user_knowledge_proposal(session))
        session["observations"] = session["observations"][:1]
        self.assertIsNone(reviewable_user_knowledge_proposal(session))

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
