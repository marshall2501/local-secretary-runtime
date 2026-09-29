from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from pkb_proto.daily_pkb import (
    CORE_UI_DEFAULT_OPEN,
    ENTITY_UI_DEFAULT_OPEN,
    FINANCE_PAGE_SIZE_DEFAULT,
    FINANCE_UI_DEFAULT_OPEN,
    PKB_UI_DEFAULT_OPEN,
    UI_VISIBILITY_DEFAULT,
    _PKB_UI_OPEN,
    _default_ui_preferences,
    _set_pkb_ui_open,
    _validate_ui_preferences,
    _contextualize_core_reply,
    _core_finance_filters,
    _compare_driver_values,
    _driver_web_query_from_detail,
    _clarified_driver_web_target,
    core_answer,
    core_task_selection_result,
    finance_core_answer,
    load_ui_preferences,
    web_core_answer,
    parse_component_write,
    parse_correction,
    parse_query,
    parse_write,
    save_ui_preferences,
    scope_core_request,
)
from pkb_proto.daily_interpreter import inspect_output
from pkb_proto.entity_model_service import classify_predicate
from pkb_proto.pending_service import acceptance_eligible


ENTITIES = {
    "メインPC": {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "メインPC",
        "domain": "pc",
        "entity_type": "computer",
    },
    "サブPC": {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "サブPC",
        "domain": "pc",
        "entity_type": "computer",
    },
    "RCカーB": {
        "id": "33333333-3333-3333-3333-333333333333",
        "name": "RCカーB",
        "domain": "rc",
        "entity_type": "rc_car",
    },
    "GPU1": {
        "id": "44444444-4444-4444-4444-444444444444",
        "name": "GPU1",
        "domain": "pc",
        "entity_type": "gpu",
    },
    "NIC1": {
        "id": "55555555-5555-5555-5555-555555555555",
        "name": "NIC1",
        "domain": "pc",
        "entity_type": "network_adapter",
    },
}


class DailyPKBParserTests(unittest.TestCase):
    def test_ui_preferences_validate_and_fall_back_per_field(self):
        prefs = _validate_ui_preferences({
            "pkb": {"write": False, "search": "invalid"},
            "entity": {"history": True, "events": "invalid"},
            "finance": {"details": True, "recent_limit": 100, "stored": 1},
            "core": {"trace": False, "screen_log": True},
            "visibility": {
                "pkb": {"limits": False, "write": "invalid"},
                "core": {"trace": False},
            },
        })
        self.assertFalse(prefs["pkb"]["write"])
        self.assertEqual(prefs["pkb"]["search"], PKB_UI_DEFAULT_OPEN["search"])
        self.assertTrue(prefs["entity"]["history"])
        self.assertEqual(
            prefs["entity"]["events"],
            ENTITY_UI_DEFAULT_OPEN["events"],
        )
        self.assertTrue(prefs["finance"]["details"])
        self.assertEqual(prefs["finance"]["stored"], FINANCE_UI_DEFAULT_OPEN["stored"])
        self.assertEqual(prefs["finance"]["recent_limit"], 100)
        self.assertFalse(prefs["core"]["trace"])
        self.assertTrue(prefs["core"]["screen_log"])
        self.assertFalse(prefs["visibility"]["pkb"]["limits"])
        self.assertEqual(
            prefs["visibility"]["pkb"]["write"],
            UI_VISIBILITY_DEFAULT["pkb"]["write"],
        )
        self.assertFalse(prefs["visibility"]["core"]["trace"])

    def test_ui_preferences_round_trip_json_outside_pkb(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ui_preferences.json"
            prefs = _default_ui_preferences()
            prefs["pkb"]["entities"] = True
            prefs["entity"]["history"] = True
            prefs["finance"]["details"] = True
            prefs["finance"]["recent_limit"] = 50
            prefs["core"]["trace"] = False
            prefs["core"]["screen_log"] = False
            prefs["visibility"]["pkb"]["limits"] = False
            prefs["visibility"]["core"]["trace"] = False
            saved = save_ui_preferences(prefs, path)
            loaded = load_ui_preferences(path)
            self.assertEqual(loaded, saved)
            self.assertTrue(loaded["pkb"]["entities"])
            self.assertTrue(loaded["entity"]["history"])
            self.assertTrue(loaded["finance"]["details"])
            self.assertEqual(loaded["finance"]["recent_limit"], 50)
            self.assertFalse(loaded["core"]["trace"])
            self.assertFalse(loaded["core"]["screen_log"])
            self.assertFalse(loaded["visibility"]["pkb"]["limits"])
            self.assertFalse(loaded["visibility"]["core"]["trace"])

    def test_ui_preferences_missing_file_uses_builtin_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            loaded = load_ui_preferences(Path(directory) / "missing.json")
        self.assertEqual(loaded["pkb"], PKB_UI_DEFAULT_OPEN)
        self.assertEqual(loaded["core"], CORE_UI_DEFAULT_OPEN)
        self.assertEqual(loaded["visibility"], UI_VISIBILITY_DEFAULT)
        self.assertEqual(
            loaded["finance"]["recent_limit"],
            FINANCE_PAGE_SIZE_DEFAULT,
        )

    def test_pkb_ui_open_state_is_centralized_and_resettable_by_process_restart(self):
        original = dict(_PKB_UI_OPEN)
        try:
            self.assertEqual(set(_PKB_UI_OPEN), set(PKB_UI_DEFAULT_OPEN))
            _set_pkb_ui_open("entities", True)
            _set_pkb_ui_open("search", False)
            self.assertTrue(_PKB_UI_OPEN["entities"])
            self.assertFalse(_PKB_UI_OPEN["search"])
        finally:
            _PKB_UI_OPEN.clear()
            _PKB_UI_OPEN.update(original)

    def test_pkb_ui_open_rejects_unknown_key(self):
        with self.assertRaises(KeyError):
            _set_pkb_ui_open("unknown", True)

    def test_core_reply_inherits_unique_entity_from_original_request(self):
        effective = _contextualize_core_reply(
            "メインPCについて調べて",
            "GPUの現在のドライバーを調べて",
            ENTITIES,
        )
        self.assertEqual(
            effective,
            "メインPCのGPUの現在のドライバーを調べて",
        )

    def test_core_reply_keeps_explicit_entity_in_reply(self):
        effective = _contextualize_core_reply(
            "メインPCについて調べて",
            "サブPCのドライバー更新履歴を見て",
            ENTITIES,
        )
        self.assertEqual(
            effective,
            "サブPCのドライバー更新履歴を見て",
        )

    def test_core_reply_does_not_guess_when_original_has_multiple_entities(self):
        effective = _contextualize_core_reply(
            "メインPCとサブPCについて調べて",
            "GPUの現在のドライバーを調べて",
            ENTITIES,
        )
        self.assertEqual(
            effective,
            "GPUの現在のドライバーを調べて",
        )

    def test_saved_waiting_task_can_be_restored_as_ui_result(self):
        result = core_task_selection_result({
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "request": "メインPCについて調べて",
            "status": "waiting_external",
            "phase": "awaiting_clarification",
            "selected_capability": None,
            "question": "確認したい内容を指定してください。",
            "effective_request": None,
        })
        self.assertEqual(
            result["task_id"],
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        self.assertEqual(result["status"], "waiting_external")
        self.assertEqual(result["phase"], "awaiting_clarification")
        self.assertTrue(result["resumed_from_storage"])
        self.assertIn("保存済みTask", result["message"])
        self.assertTrue(result["question"])

    def test_saved_task_selection_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            core_task_selection_result({
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "status": "mystery",
            })

    def test_core_scope_routes_current_vs_latest_driver_to_pkb_web_compare(self):
        result = scope_core_request(
            "メインPCのGPUの現在のドライバーをPKBで確認し、Webの最新と比較して",
            ENTITIES,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["capability"], "pkb_web_compare")
        self.assertEqual(result["domain"], "pc")

    def test_core_scope_routes_explicit_web_request_to_web_research(self):
        result = scope_core_request(
            "RX 9070 XTの最新ドライバーをWebで調べて",
            ENTITIES,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["capability"], "web_research")
        self.assertEqual(result["domain"], "research")

    def test_web_core_answer_uses_search_titles_and_snippets(self):
        answer = web_core_answer({
            "hits": [
                {
                    "title": "AMD Drivers",
                    "url": "https://example.com/amd",
                    "snippet": "Latest driver information.",
                },
                {
                    "title": "Release Notes",
                    "url": "https://example.com/release",
                    "snippet": "Release notes.",
                },
            ]
        })
        self.assertIn("AMD Drivers", answer)
        self.assertIn("Latest driver information.", answer)
        self.assertIn("Release Notes", answer)

    def test_clarified_driver_target_builds_bounded_web_query(self):
        target = _clarified_driver_web_target("GPUモデルは Radeon RX 9070 XT")
        self.assertEqual(target["status"], "ready")
        self.assertEqual(
            target["query"],
            "Radeon RX 9070 XT latest driver official",
        )
        self.assertTrue(target["clarified_by_user"])

    def test_clarified_driver_target_rejects_empty_label_only_reply(self):
        target = _clarified_driver_web_target("GPUモデルは")
        self.assertEqual(target["status"], "missing_model")
        self.assertIsNone(target["query"])

    def test_driver_web_query_prefers_authoritative_model_attributes(self):
        target = _driver_web_query_from_detail({
            "entity": {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "name": "GPU1",
                "domain": "pc",
                "entity_type": "gpu",
            },
            "current": [
                {
                    "predicate": "manufacturer",
                    "value": "AMD",
                    "semantic_kind": "attribute",
                },
                {
                    "predicate": "model",
                    "value": "Radeon RX 9070 XT",
                    "semantic_kind": "attribute",
                },
            ],
        })
        self.assertEqual(target["status"], "ready")
        self.assertEqual(
            target["query"],
            "AMD Radeon RX 9070 XT latest driver official",
        )
        self.assertEqual(target["model"], "Radeon RX 9070 XT")

    def test_driver_web_query_refuses_generic_entity_without_model(self):
        target = _driver_web_query_from_detail({
            "entity": {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "name": "GPU1",
                "domain": "pc",
                "entity_type": "gpu",
            },
            "current": [
                {
                    "predicate": "current_driver",
                    "value": "DRV-G3",
                    "semantic_kind": "state",
                },
            ],
        })
        self.assertEqual(target["status"], "missing_model")
        self.assertIsNone(target["query"])

    def test_compare_driver_values_reports_match_for_same_version_format(self):
        comparison = _compare_driver_values(
            {
                "items": [{"current_driver": "26.9.1"}],
            },
            {
                "fact_summary": {
                    "best_candidate": "26.9.1",
                    "preferred_kind": "adrenalin_version",
                    "status": "latest_by_date",
                }
            },
        )
        self.assertEqual(comparison["status"], "match")
        self.assertEqual(comparison["current"], "26.9.1")
        self.assertEqual(comparison["latest"], "26.9.1")

    def test_compare_driver_values_refuses_incompatible_fixture_value(self):
        comparison = _compare_driver_values(
            {
                "items": [{"current_driver": "DRV-G3"}],
            },
            {
                "fact_summary": {
                    "best_candidate": "26.9.1",
                    "preferred_kind": "adrenalin_version",
                    "status": "latest_by_date",
                }
            },
        )
        self.assertEqual(comparison["status"], "not_comparable")
        self.assertIn("安全に比較できません", comparison["message"])

    def test_web_core_answer_does_not_overstate_conflicting_driver_candidates(self):
        answer = web_core_answer({
            "hits": [],
            "fact_summary": {
                "kind": "driver_version",
                "status": "conflicting_candidates",
                "preferred_kind": "driver_version",
                "best_candidate": "26.9.1",
                "groups": [
                    {
                        "kind": "driver_version",
                        "status": "conflicting_candidates",
                        "best_candidate": "26.9.1",
                        "candidates": [
                            {"value": "26.9.1"},
                            {"value": "26.8.1"},
                        ],
                    }
                ],
                "historical_groups": [],
            },
        })
        self.assertIn("一致していません", answer)
        self.assertIn("26.9.1", answer)
        self.assertIn("26.8.1", answer)
        self.assertIn("追加確認", answer)

    def test_web_core_answer_marks_single_candidate_as_unconfirmed(self):
        answer = web_core_answer({
            "hits": [{"title": "AMD Drivers", "url": "https://amd.example"}],
            "fact_summary": {
                "kind": "driver_version",
                "status": "single_candidate",
                "preferred_kind": "driver_version",
                "best_candidate": "26.9.1",
                "groups": [
                    {
                        "kind": "driver_version",
                        "status": "single_candidate",
                        "best_candidate": "26.9.1",
                        "candidates": [{"value": "26.9.1", "source_count": 1}],
                    },
                    {
                        "kind": "si_driver_version",
                        "status": "single_candidate",
                        "best_candidate": "25.10.2",
                        "candidates": [{"value": "25.10.2", "source_count": 1}],
                    },
                ],
                "historical_groups": [],
            },
        })
        self.assertIn("26.9.1", answer)
        self.assertIn("確定値とは扱いません", answer)
        self.assertIn("si_driver_version=25.10.2", answer)

    def test_core_scope_routes_finance_request_to_finance_read(self):
        result = scope_core_request("2026年9月の支出を調べて", ENTITIES)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["capability"], "finance_read")
        self.assertEqual(result["domain"], "finance")

    def test_core_finance_filters_parse_explicit_month(self):
        filters = _core_finance_filters("2026年9月の家計を確認して")
        self.assertEqual(filters["start_date"], "2026-09-01")
        self.assertEqual(filters["end_date"], "2026-09-30")
        self.assertEqual(filters["row_mode"], "calculation_target")

    def test_finance_core_answer_uses_requested_period_not_observed_span(self):
        answer = finance_core_answer({
            "total": 2,
            "transaction_count": 2,
            "requested_start_date": "2026-09-01",
            "requested_end_date": "2026-09-30",
            "data_start_date": "2026-09-03",
            "data_end_date": "2026-09-28",
            "income_total": 0,
            "expense_total": 1234,
            "net_total": -1234,
        })
        self.assertIn("2026-09-01〜2026-09-30", answer)
        self.assertNotIn("2026-09-03〜2026-09-28", answer)

    def test_finance_core_answer_formats_deterministic_totals(self):
        answer = finance_core_answer({
            "total": 3,
            "transaction_count": 3,
            "requested_start_date": "2026-09-01",
            "requested_end_date": "2026-09-30",
            "data_start_date": "2026-09-02",
            "data_end_date": "2026-09-28",
            "income_total": 100000,
            "expense_total": 25000,
            "net_total": 75000,
        })
        self.assertIn("3件", answer)
        self.assertIn("¥100,000", answer)
        self.assertIn("¥25,000", answer)
        self.assertIn("¥75,000", answer)

    def test_core_scope_accepts_bounded_component_state_query(self):
        result = scope_core_request(
            "メインPCのGPUの現在のドライバーを調べて",
            ENTITIES,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["capability"], "pkb_search")
        self.assertEqual(result["domain"], "pc")

    def test_core_scope_asks_when_request_is_not_safely_scoped(self):
        result = scope_core_request("何か調べて", ENTITIES)
        self.assertEqual(result["status"], "question")
        self.assertEqual(result["reason"], "request_not_safely_scoped")
        self.assertTrue(result["question"])

    def test_core_answer_preserves_claim_identity_in_summary(self):
        answer = core_answer({
            "result_kind": "claims",
            "total": 1,
            "items": [{
                "entity_name": "GPU1",
                "predicate": "current_driver",
                "value": "DRV-G3",
            }],
        })
        self.assertIn("GPU1", answer)
        self.assertIn("current_driver=DRV-G3", answer)

    def test_explicit_pc_update(self):
        result = parse_write("メインPCをDRV-A3へ更新した。", set(ENTITIES))
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["entity"], "メインPC")
        self.assertEqual(result["predicate"], "driver_updated")
        self.assertEqual(result["value"], "DRV-A3")

    def test_explicit_rc_servo_replacement(self):
        result = parse_write("RCカーBのサーボをSERVO-X3へ交換した。", set(ENTITIES))
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["entity"], "RCカーB")
        self.assertEqual(result["predicate"], "servo_updated")
        self.assertEqual(result["value"], "SERVO-X3")

    def test_unknown_entity_goes_to_review(self):
        result = parse_write("未知PCをDRV-Xへ更新した。", set(ENTITIES))
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["reason"], "unknown_or_ambiguous_entity")

    def test_explicit_correction(self):
        result = parse_correction(
            "訂正：サブPCではなくメインPCをDRV-A1へ更新した。",
            set(ENTITIES),
        )
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["old_entity"], "サブPC")
        self.assertEqual(result["new_entity"], "メインPC")
        self.assertEqual(result["value"], "DRV-A1")

    def test_query_uses_known_entity_and_history(self):
        query = parse_query("サブPCのドライバー更新履歴", ENTITIES)
        self.assertEqual(
            query.entity_id,
            UUID("22222222-2222-2222-2222-222222222222"),
        )
        self.assertEqual(query.predicate, "driver_updated")
        self.assertTrue(query.include_history)

    def test_query_without_known_terms_stays_scoped_only_by_available_filters(self):
        query = parse_query("全部の記録", ENTITIES)
        self.assertIsNone(query.entity_id)
        self.assertIsNone(query.predicate)
        self.assertTrue(query.include_history)

    def test_structured_conflict_is_acceptance_eligible(self):
        row = {
            "review_status": "pending",
            "reason": "existing_claim_requires_conflict_resolution",
            "entity_id": UUID("22222222-2222-2222-2222-222222222222"),
            "entity_name": "サブPC",
            "predicate": "driver_updated",
            "proposed_value": "DRV-A9",
            "raw_text": "サブPCをDRV-A9へ更新した。",
        }
        self.assertTrue(acceptance_eligible(row))

    def test_unstructured_pending_is_not_acceptance_eligible(self):
        row = {
            "review_status": "pending",
            "reason": "unsupported_natural_language_in_first_slice",
            "entity_id": None,
            "entity_name": None,
            "predicate": None,
            "proposed_value": None,
            "raw_text": "サブPCの調子が最近よくない気がする。",
        }
        self.assertFalse(acceptance_eligible(row))

    def test_daily_interpreter_grounded_candidate(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z10",
                "quote": "メインPCのドライバーをDRV-Z10へ更新した",
            }
        }
        result = inspect_output(
            "今日はメインPCのドライバーをDRV-Z10へ更新した。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "candidate")
        self.assertEqual(result.candidate.entity_mention, "メインPC")
        self.assertEqual(result.candidate.value, "DRV-Z10")

    def test_daily_interpreter_uncertainty_is_not_candidate(self):
        raw = {
            "candidate": {
                "entity_mention": "サブPC",
                "predicate": "driver_updated",
                "value": "DRV-X",
                "quote": "サブPCをDRV-Xへ更新したかもしれない",
            }
        }
        result = inspect_output(
            "サブPCをDRV-Xへ更新したかもしれない。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "no_candidate")

    def test_daily_interpreter_multiple_entities_is_invalid(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z10",
                "quote": "メインPCをDRV-Z10へ更新した",
            }
        }
        result = inspect_output(
            "メインPCをDRV-Z10へ更新した。サブPCも確認した。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "invalid")

    def test_model_candidate_is_acceptance_eligible_only_with_provenance(self):
        row = {
            "review_status": "pending",
            "reason": "model_candidate_needs_user_confirmation",
            "entity_id": UUID("11111111-1111-1111-1111-111111111111"),
            "entity_name": "メインPC",
            "predicate": "driver_updated",
            "proposed_value": "DRV-Z10",
            "raw_text": "メインPCをDRV-Z10へ更新した。",
            "interpreter_kind": "local_ollama",
            "interpreter_model": "llama3.1:8b",
        }
        self.assertTrue(acceptance_eligible(row))
        row["interpreter_model"] = None
        self.assertFalse(acceptance_eligible(row))

    def test_daily_interpreter_bounded_natural_update_wording(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z11",
                "quote": "メインPCのドライバーをDRV-Z11へ更新しておいた",
            }
        }
        result = inspect_output(
            "今日はメインPCのドライバーをDRV-Z11へ更新しておいた。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "candidate")

    def test_model_candidate_natural_wording_is_acceptance_eligible(self):
        row = {
            "review_status": "pending",
            "reason": "model_candidate_needs_user_confirmation",
            "entity_id": UUID("11111111-1111-1111-1111-111111111111"),
            "entity_name": "メインPC",
            "predicate": "driver_updated",
            "proposed_value": "DRV-Z11",
            "raw_text": "今日はメインPCのドライバーをDRV-Z11へ更新しておいた。",
            "interpreter_kind": "local_ollama",
            "interpreter_model": "llama3.1:8b",
        }
        self.assertTrue(acceptance_eligible(row))

    def test_entity_model_semantic_classification(self):
        self.assertEqual(classify_predicate("driver_updated"), "event")
        self.assertEqual(classify_predicate("servo_updated"), "event")
        self.assertEqual(classify_predicate("current_driver"), "state")
        self.assertEqual(classify_predicate("manufacturer"), "attribute")
        self.assertIsNone(classify_predicate("unknown_predicate"))

    def test_daily_interpreter_recovers_exact_source_quote(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z13",
                "quote": "メインPCのドライバーをDRV-Z13へ更新しておいたです",
            }
        }
        result = inspect_output(
            "さっきメインPCのドライバーをDRV-Z13へ更新しておいた。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "candidate")
        self.assertEqual(result.candidate.value, "DRV-Z13")
        self.assertEqual(
            result.candidate.quote,
            "さっきメインPCのドライバーをDRV-Z13へ更新しておいた。",
        )

    def test_daily_interpreter_rejects_multiple_grounded_update_sentences(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z13",
                "quote": "dummy",
            }
        }
        result = inspect_output(
            "メインPCをDRV-Z13へ更新した。メインPCをDRV-Z14へ更新した。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason, "grounded_sentence_not_unique")

    def test_component_write_parses_parent_and_role_not_embedded_entity_name(self):
        result = parse_component_write(
            "メインPCのGPUドライバーをDRV-G1へ更新した。"
        )
        self.assertEqual(result["parent"], "メインPC")
        self.assertEqual(result["role"], "GPU")
        self.assertEqual(result["predicate"], "driver_updated")
        self.assertEqual(result["value"], "DRV-G1")

    def test_normalized_component_entities_do_not_embed_parent_name(self):
        self.assertIn("GPU1", ENTITIES)
        self.assertIn("NIC1", ENTITIES)
        self.assertNotIn("メインPCのGPU", ENTITIES)
        self.assertNotIn("メインPCのNIC", ENTITIES)

    def test_current_driver_query_uses_effective_time(self):
        query = parse_query("GPU1の現在のドライバー", ENTITIES)
        self.assertEqual(
            query.entity_id,
            UUID("44444444-4444-4444-4444-444444444444"),
        )
        self.assertEqual(query.predicate, "current_driver")
        self.assertIsNotNone(query.effective_at)
        self.assertIsNotNone(query.effective_at.utcoffset())


if __name__ == "__main__":
    unittest.main()
