# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Isolated, fictional PKB structured write checks; no operational database."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pkb.application.structured_claim import register_structured_claim
from pkb.ingestion_gate import assess, Route
from pkb.write_service import (
    WriteResult, literal_gate, payload_hash, normalize_explicit_attribute_evidence,
)

TASK_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OPERATION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ENTITY_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
NOW = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
ORIGINAL = "架空GPUをDRV-200へ更新した。"


class FakePkbRepository:
    """Mirrors the existing PKB gates without any real DB or LLM calls."""

    def __init__(self):
        self.payloads = {}
        self.calls = 0

    def source_ref(self, prefix, identifier):
        return f"local://{prefix}/{identifier}"

    def write_one(self, record, claim):
        self.calls += 1
        outcome = assess(
            record, claim,
            aliases={ENTITY_ID: {"架空GPU"}},
        )
        if outcome.route is not Route.AUTO_CANDIDATE:
            return WriteResult(outcome.route.value.replace("auto_candidate", "inserted"), outcome.reason)
        issue = literal_gate(record, claim)
        if issue:
            return WriteResult("review", issue)
        digest = payload_hash(record, claim)
        previous = self.payloads.get(record.input_id)
        if previous:
            if previous != digest:
                return WriteResult("rejected", "input_id_reused_with_different_payload")
            return WriteResult("replayed", "same_input_already_written", "claim-1", "source-1")
        self.payloads[record.input_id] = digest
        return WriteResult("inserted", "single_claim_committed", "claim-1", "source-1")


def write(repository, **overrides):
    arguments = {
        "task_id": TASK_ID,
        "invocation_id": OPERATION_ID,
        "user_text": ORIGINAL,
        "entity_id": ENTITY_ID,
        "entity_mention": "架空GPU",
        "predicate": "driver_updated",
        "value": "DRV-200",
        "evidence_start": 0,
        "evidence_end": len(ORIGINAL),
        "recorded_at": NOW,
        "occurred_at": NOW,
    }
    arguments.update(overrides)
    return register_structured_claim(repository, **arguments)


class StructuredClaimTests(unittest.TestCase):
    def test_register_replay_and_payload_conflict_keep_one_claim(self):
        repo = FakePkbRepository()
        first = write(repo)
        again = write(repo)
        conflict = write(repo, recorded_at=NOW + timedelta(seconds=1))
        self.assertEqual(first["status"], "inserted")
        self.assertEqual(again["status"], "replayed")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(len(repo.payloads), 1)
        self.assertEqual(first["claim_id"], again["claim_id"])

    def test_explicit_model_and_manufacturer_attributes_register_safely(self):
        """登録済みEntityへの明示属性は既存Receiptで冪等に保存する。"""
        for predicate, original, value in (
            ("model", "架空GPUの機種はRTX 4070 Ti SUPER。", "RTX 4070 Ti SUPER"),
            ("manufacturer", "架空GPUのメーカーはNVIDIA。", "NVIDIA"),
        ):
            with self.subTest(predicate=predicate):
                repo = FakePkbRepository()
                args = dict(
                    user_text=original, evidence_end=len(original),
                    predicate=predicate, value=value,
                )
                result = write(repo, **args)
                replay = write(repo, **args)
                self.assertEqual(result["status"], "inserted")
                self.assertEqual(replay["status"], "replayed")
                self.assertEqual(result["claim_id"], replay["claim_id"])
                self.assertEqual(len(repo.payloads), 1)

    def test_unique_labeled_attribute_can_repair_only_quote_boundaries(self):
        """LLM文字数の誤りだけを、本人原文の一意な明示文から補正する。"""
        raw = "架空GPUの機種はRTX-SYNTH-400。"
        args = {
            "entity_mention": "架空GPU", "predicate": "model",
            "value": "RTX-SYNTH-400",
            "evidence_start": 0, "evidence_end": 15,
        }
        normalized = normalize_explicit_attribute_evidence(raw, args)
        self.assertEqual((normalized["evidence_start"], normalized["evidence_end"]),
                         (0, len(raw) - 1))
        self.assertEqual(args["evidence_end"], 15)
        self.assertEqual(normalize_explicit_attribute_evidence(raw, normalized), normalized)

    def test_never_repair_forged_or_ambiguous_attribute(self):
        """推測・複数値・不確実申告・別のEntityへの値混用を許さない。"""
        args = {
            "entity_mention": "架空GPU", "predicate": "model",
            "value": "RTX-SYNTH-400", "evidence_start": 0,
            "evidence_end": 15,
        }
        for raw, overrides in (
            ("架空GPUの機種はRTX-OTHER。", {}),
            ("架空GPUの機種はRTX-SYNTH-400かもしれない。", {}),
            ("架空GPUの機種はRTX-SYNTH-400。機種はRTX-OTHER。", {}),
            ("架空GPUの機種はRTX-SYNTH-400。架空GPUの機種はRTX-SYNTH-400。", {}),
            ("架空GPUの機種はRTX-SYNTH-400。", {"entity_mention": "別のGPU"}),
            ("別のGPUの機種はRTX-SYNTH-400。架空GPUです。", {}),
            ("架空GPUの機種はRTX-SYNTH-400。", {"value": "MADE-UP"}),
            ("架空GPUの機種はRTX-SYNTH-400。", {"predicate": "driver_updated"}),
            ("架空GPUの機種はRTX-SYNTH-400。", {"evidence_start": -1}),
            ("架空GPUの機種はRTX-SYNTH-400。", {"evidence_end": 999}),
        ):
            with self.subTest(raw=raw, overrides=overrides):
                selected = {**args, **overrides}
                self.assertEqual(
                    normalize_explicit_attribute_evidence(raw, selected),
                    selected,
                )

    def test_attribute_value_must_follow_matching_label(self):
        """モデルが属性と別の値を対応付けても自動登録を拒否する。"""
        for predicate, original, value, expected in (
            ("model", "架空GPUのメーカーはNVIDIA。", "NVIDIA",
             "value_or_attribute_not_explicit_in_quote"),
            ("model", "架空GPUの機種はRTX-100、メーカーはNVIDIA。", "NVIDIA",
             "value_or_attribute_not_explicit_in_quote"),
            ("manufacturer", "架空GPUの機種はRTX-100。", "RTX-100",
             "value_or_attribute_not_explicit_in_quote"),
            ("model", "架空GPUの機種はRTX-100、型番はRTX-200。", "RTX-100",
             "ambiguous_attribute_statement"),
        ):
            with self.subTest(original=original, predicate=predicate):
                repo = FakePkbRepository()
                result = write(
                    repo, user_text=original, evidence_end=len(original),
                    predicate=predicate, value=value,
                )
                self.assertEqual(result["status"], "review")
                self.assertEqual(result["reason"], expected)
                self.assertEqual(repo.payloads, {})

    def test_uncertainty_outside_quoted_span_still_blocks_attribute(self):
        """狭い引用範囲で未確認表現を落としても登録は保留する。"""
        original = "架空GPUの機種はRTX-100。未確認です。"
        span = original.index("。") + 1
        repo = FakePkbRepository()
        result = write(
            repo, user_text=original, evidence_end=span,
            predicate="model", value="RTX-100",
        )
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["reason"], "uncertain_or_correction_language")
        self.assertEqual(repo.payloads, {})

    def test_source_and_evidence_do_not_depend_on_model_assertions(self):
        repo = FakePkbRepository()
        self.assertEqual(write(repo)["status"], "inserted")
        self.assertEqual(len(repo.payloads), 1)
        for override, error in (
            ({"task_id": "model-provided-id"}, "invalid_structured_claim_identity"),
            ({"invocation_id": "arbitrary"}, "invalid_structured_claim_identity"),
            ({"predicate": "has_component"}, "unsupported_structured_claim"),
            ({"entity_mention": "架空GPU以外"}, "structured_claim_not_grounded_in_user_text"),
            ({"evidence_start": -1}, "invalid_structured_claim_evidence"),
            ({"recorded_at": datetime(2026, 10, 20)}, "structured_claim_times_must_be_persisted_and_aware"),
        ):
            with self.subTest(override=override):
                with self.assertRaisesRegex(ValueError, error):
                    write(repo, **override)
        self.assertEqual(len(repo.payloads), 1)

    def test_uncertain_user_statement_needs_review(self):
        repo = FakePkbRepository()
        tentative = "架空GPUをDRV-200へ更新したかもしれない。"
        result = write(repo, user_text=tentative, evidence_end=len(tentative))
        self.assertEqual(result["status"], "review")
        self.assertEqual(repo.payloads, {})

    def test_new_task_invocation_identity_is_independent(self):
        repo = FakePkbRepository()
        first = write(repo)
        second = write(repo, invocation_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        self.assertEqual(first["status"], "inserted")
        self.assertEqual(second["status"], "inserted")
        self.assertEqual(len(repo.payloads), 2)


if __name__ == "__main__":
    unittest.main()
