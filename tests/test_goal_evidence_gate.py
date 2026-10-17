# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Goalの回答候補とTask確定が同じRITSUKO根拠検査を使う回帰試験。"""
from __future__ import annotations

import unittest

from ritsuko.core.goal_evidence import check_goal_response_evidence
from ritsuko.core.magi_bridge import task_projection


class GoalEvidenceGateTests(unittest.TestCase):
    """架空LLMがResult IDを偽装してもTaskを完了させない。"""

    @staticmethod
    def session(
        basis: str, ids: list[str], *, observations: list[dict] | None = None,
        requires_source: bool = False, tool_read_executed: bool = False,
    ) -> dict:
        """同一の確定Task投影で使うGoal候補を作る。"""
        return {
            "decision_strategy": "llm", "status": "candidate_ready",
            "requires_source": requires_source,
            "tool_read_executed": tool_read_executed,
            "observations": observations or [], "pending_requests": [],
            "goal_judgment": {
                "next": "respond", "fulfillment": "met",
                "answer": "回答候補",
                "evidence": {"basis": basis, "result_ids": ids},
            },
        }

    def test_verified_result_is_the_only_usable_recorded_reference(self):
        """未記録・失敗・偽Sourceの結果IDをRITSUKOが拒否する。"""
        observations = [
            {"result_id": "ok", "verified": True, "source": "web",
             "verification_level": "provider_cited"},
            {"result_id": "failed", "verified": False, "source": "finance"},
            {"result_id": "fake", "verified": True, "source": "external_service"},
        ]
        for ids in (["unknown"], ["failed"], ["fake"], ["ok", "unknown"]):
            with self.subTest(ids=ids):
                session = self.session("result_refs", ids, observations=observations)
                status, refs = check_goal_response_evidence(
                    session, session["goal_judgment"]["evidence"],
                )
                self.assertEqual(status, "unrecorded_task_result_reference")
                self.assertEqual(refs, {})
                self.assertNotEqual(task_projection(session)["task_status"], "completed")
        allowed = self.session("result_refs", ["ok"], observations=observations)
        self.assertEqual(
            task_projection(allowed)["reason"], "goal_evaluated_with_recorded_evidence",
        )
        self.assertIn("Provider", task_projection(allowed)["message"])

    def test_source_free_is_allowed_only_before_any_required_read(self):
        """出典が不要な説明は完了するが必要な読取は省略できない。"""
        public = self.session("not_required", [])
        status, refs = check_goal_response_evidence(
            public, public["goal_judgment"]["evidence"],
        )
        self.assertEqual((status, refs), ("source_free", {}))
        self.assertEqual(task_projection(public)["task_status"], "completed")

        for kw in ({"requires_source": True}, {"tool_read_executed": True}):
            with self.subTest(kw=kw):
                blocked = self.session("not_required", [], **kw)
                status, _ = check_goal_response_evidence(
                    blocked, blocked["goal_judgment"]["evidence"],
                )
                self.assertEqual(status, "required_source_not_observed")
                self.assertNotEqual(
                    task_projection(blocked)["task_status"], "completed",
                )

    def test_mismatched_evidence_basis_never_completes(self):
        """根拠なし・余分な参照IDとbasisの矛盾を完了扱いしない。"""
        for basis, ids in (
            ("result_refs", []), ("not_required", ["known"]),
            ("insufficient", []),
        ):
            with self.subTest(basis=basis):
                s = self.session(basis, ids, observations=[
                    {"result_id": "known", "verified": True, "source": "pkb"},
                ])
                self.assertNotEqual(task_projection(s)["task_status"], "completed")


if __name__ == "__main__":
    unittest.main()
