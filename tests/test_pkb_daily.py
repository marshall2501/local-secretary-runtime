from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from uuid import UUID

from interfaces.web.app import (
    CORE_UI_DEFAULT_OPEN,
    ENTITY_UI_DEFAULT_OPEN,
    ENTITY_TAB_DEFAULT,
    ENTITY_TAB_DEFAULT_VISIBLE,
    ENTITY_TAB_ORDER,
    FINANCE_PAGE_SIZE_DEFAULT,
    FINANCE_UI_DEFAULT_OPEN,
    PKB_UI_DEFAULT_OPEN,
    UI_VISIBILITY_DEFAULT,
    _PKB_UI_OPEN,
    _advisor_log_export,
    _memory_intake_log_export,
    _default_ui_preferences,
    _entity_source_rows,
    _entity_tab_config,
    _set_pkb_ui_open,
    _validate_ui_preferences,
    _contextualize_core_reply,
    _core_finance_filters,
    _compare_driver_values,
    _driver_web_query_from_detail,
    _execute_cooperative_local_probe,
    _execute_magi_pkb_request,
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
from pkb.daily_interpreter import inspect_output
from pkb.memory_contracts import MemoryIntake
from pkb.entity_model_service import classify_predicate
from pkb.pending_service import acceptance_eligible


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
    def test_advisor_log_export_contains_copyable_magi_context(self):
        payload = _advisor_log_export(
            {
                "task_id": "task-1",
                "status": "completed",
                "phase": "completed",
                "question": None,
                "selected_capability": "pkb_search",
                "observation_pack": {"version": "magi_observation_v1"},
            },
            {
                "model": "gemma3:12b",
                "timeout_seconds": 900,
                "job_status": "completed",
                "status": "ok",
                "elapsed_seconds": 66.2,
                "next_step": "observe",
                "proposed_action": "pkb_search",
                "comparison": "match",
                "missing_information": [],
                "request_context": {"magi_member": "CASPER"},
                "response_diagnostic": {"json_valid": True},
            },
            {
                "advisor_events": [
                    {"event_type": "core.advisor.running", "occurred_at": "t1"},
                    {"event_type": "core.advisor.completed", "occurred_at": "t2"},
                ],
                "task": {
                    "request": "メインPCのGPUの現在のドライバーを調べて",
                    "magi_baseline": {
                        "member": "MELCHIOR",
                        "status": "ready",
                        "selected_capability": "pkb_search",
                    },
                },
            },
        )
        self.assertEqual(payload["task_id"], "task-1")
        self.assertEqual(payload["magi"]["melchior_baseline"], "pkb_search")
        self.assertEqual(payload["magi"]["casper_next_step"], "observe")
        self.assertEqual(payload["magi"]["casper_proposal"], "pkb_search")
        self.assertEqual(payload["magi"]["comparison"], "match")
        self.assertEqual(payload["magi"]["melchior_scope_status"], "ready")
        self.assertEqual(payload["core"]["status"], "completed")
        self.assertEqual(payload["core"]["phase"], "completed")
        self.assertIsNone(payload["core"]["question"])
        self.assertEqual(payload["advisor"]["model"], "gemma3:12b")
        self.assertEqual(len(payload["state_transitions"]), 2)
        self.assertEqual(
            payload["observation_pack"]["version"],
            "magi_observation_v1",
        )

    def test_memory_intake_log_export_contains_grounding_and_write_ids(self):
        envelope = MemoryIntake.issue('サブPCのWindows11を26H2に上げた。')
        payload = _memory_intake_log_export(
            envelope,
            {
                'status': 'committed',
                'source_id': 'source-1',
                'candidates': [
                    {
                        'candidate_id': '1',
                        'decision': 'auto_commit',
                        'status': 'valid',
                        'reason': 'explicit_supported_statement',
                        'claim_id': 'claim-1',
                        'derived_claim_ids': ['state-1'],
                        'pending_id': None,
                        'audit': {
                            'draft': {'predicate': {'concept_hint': 'os.upgrade'}},
                            'grounding': {
                                'entity_id': 'entity-1',
                                'predicate': 'os_release_changed',
                            },
                        },
                    }
                ],
            },
        )
        self.assertEqual(payload['input']['raw_text'], 'サブPCのWindows11を26H2に上げた。')
        self.assertEqual(payload['write_result']['status'], 'committed')
        self.assertEqual(payload['write_result']['source_id'], 'source-1')
        candidate = payload['candidates'][0]
        self.assertEqual(candidate['decision'], 'auto_commit')
        self.assertEqual(candidate['claim_id'], 'claim-1')
        self.assertEqual(candidate['derived_claim_ids'], ['state-1'])
        self.assertEqual(candidate['grounding']['predicate'], 'os_release_changed')
        self.assertNotIn('reasoning', payload)

    def test_ui_preferences_validate_and_fall_back_per_field(self):
        prefs = _validate_ui_preferences({
            "pkb": {"write": False, "search": "invalid"},
            "entity": {"default_tab": "invalid"},
            "finance": {"details": True, "recent_limit": 100, "stored": 1},
            "core": {"trace": False, "screen_log": True},
            "core_advisor_model": "gemma4:12b",
            "core_advisor_timeout": 180,
            "visibility": {
                "pkb": {"limits": False, "write": "invalid"},
                "entity": {
                    "overview": True,
                    "history": "invalid",
                    "relations": False,
                    "sources": True,
                },
                "core": {"trace": False},
            },
        })

        self.assertFalse(prefs["pkb"]["write"])
        self.assertEqual(
            prefs["pkb"]["search"],
            PKB_UI_DEFAULT_OPEN["search"],
        )

        self.assertEqual(
            prefs["entity"]["default_tab"],
            ENTITY_TAB_DEFAULT,
        )
        self.assertTrue(
            prefs["visibility"]["entity"]["overview"]
        )
        self.assertEqual(
            prefs["visibility"]["entity"]["history"],
            ENTITY_TAB_DEFAULT_VISIBLE["history"],
        )
        self.assertFalse(
            prefs["visibility"]["entity"]["relations"]
        )
        self.assertTrue(
            prefs["visibility"]["entity"]["sources"]
        )

        self.assertTrue(prefs["finance"]["details"])
        self.assertEqual(
            prefs["finance"]["stored"],
            FINANCE_UI_DEFAULT_OPEN["stored"],
        )
        self.assertEqual(prefs["finance"]["recent_limit"], 100)

        self.assertFalse(prefs["core"]["trace"])
        self.assertTrue(prefs["core"]["screen_log"])
        self.assertEqual(
            prefs["core_advisor_model"],
            "gemma4:12b",
        )
        self.assertEqual(
            prefs["core_advisor_timeout"],
            180,
        )

        self.assertFalse(
            prefs["visibility"]["pkb"]["limits"]
        )
        self.assertEqual(
            prefs["visibility"]["pkb"]["write"],
            UI_VISIBILITY_DEFAULT["pkb"]["write"],
        )
        self.assertFalse(
            prefs["visibility"]["core"]["trace"]
        )

    def test_ui_preferences_round_trip_json_outside_pkb(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ui_preferences.json"

            prefs = _default_ui_preferences()
            prefs["pkb"]["entities"] = True

            prefs["entity"]["default_tab"] = "history"
            prefs["visibility"]["entity"]["sources"] = False

            prefs["finance"]["details"] = True
            prefs["finance"]["recent_limit"] = 50

            prefs["core"]["trace"] = False
            prefs["core"]["screen_log"] = False
            prefs["core_advisor_model"] = "gpt-oss:20b"
            prefs["core_advisor_timeout"] = 120

            prefs["visibility"]["pkb"]["limits"] = False
            prefs["visibility"]["core"]["trace"] = False

            saved = save_ui_preferences(prefs, path)
            loaded = load_ui_preferences(path)

            self.assertEqual(loaded, saved)

            self.assertTrue(
                loaded["pkb"]["entities"]
            )

            self.assertEqual(
                loaded["entity"]["default_tab"],
                "history",
            )
            self.assertFalse(
                loaded["visibility"]["entity"]["sources"]
            )

            self.assertTrue(
                loaded["finance"]["details"]
            )
            self.assertEqual(
                loaded["finance"]["recent_limit"],
                50,
            )

            self.assertFalse(
                loaded["core"]["trace"]
            )
            self.assertFalse(
                loaded["core"]["screen_log"]
            )
            self.assertEqual(
                loaded["core_advisor_model"],
                "gpt-oss:20b",
            )
            self.assertEqual(
                loaded["core_advisor_timeout"],
                120,
            )
            self.assertFalse(
                loaded["visibility"]["pkb"]["limits"]
            )
            self.assertFalse(
                loaded["visibility"]["core"]["trace"]
            )

    def test_ui_preferences_missing_file_uses_builtin_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            loaded = load_ui_preferences(
                Path(directory) / "missing.json"
            )

        self.assertEqual(
            loaded["pkb"],
            PKB_UI_DEFAULT_OPEN,
        )

        self.assertEqual(
            loaded["entity"],
            {"default_tab": ENTITY_TAB_DEFAULT},
        )
        self.assertEqual(
            loaded["visibility"]["entity"],
            ENTITY_TAB_DEFAULT_VISIBLE,
        )

        self.assertEqual(
            loaded["core"],
            {
                **CORE_UI_DEFAULT_OPEN,
                "open_limit": 5,
                "completed_limit": 5,
            },
        )
        self.assertEqual(
            loaded["visibility"],
            UI_VISIBILITY_DEFAULT,
        )
        self.assertEqual(
            loaded["finance"]["recent_limit"],
            FINANCE_PAGE_SIZE_DEFAULT,
        )
        self.assertEqual(
            loaded["core_advisor_timeout"],
            60,
        )

    def test_legacy_entity_preferences_migrate_to_tabs(self):
        prefs = _validate_ui_preferences({
            "entity": {
                "current": False,
                "relations": True,
                "events": False,
                "history": True,
            },
            "visibility": {
                "entity": {
                    "current": False,
                    "relations": True,
                    "events": False,
                    "history": True,
                },
            },
        })

        self.assertEqual(
            prefs["entity"]["default_tab"],
            "history",
        )
        self.assertFalse(
            prefs["visibility"]["entity"]["overview"]
        )
        self.assertTrue(
            prefs["visibility"]["entity"]["history"]
        )
        self.assertTrue(
            prefs["visibility"]["entity"]["relations"]
        )
        self.assertTrue(
            prefs["visibility"]["entity"]["sources"]
        )

    def test_entity_preferences_prevent_all_tabs_hidden(self):
        prefs = _validate_ui_preferences({
            "entity": {
                "default_tab": "sources",
            },
            "visibility": {
                "entity": {
                    "overview": False,
                    "history": False,
                    "relations": False,
                    "sources": False,
                },
            },
        })

        self.assertTrue(
            prefs["visibility"]["entity"]["overview"]
        )
        self.assertEqual(
            prefs["entity"]["default_tab"],
            "overview",
        )
        self.assertTrue(
            any(prefs["visibility"]["entity"].values())
        )

    def test_entity_hidden_default_tab_falls_back_to_first_visible(self):
        prefs = _validate_ui_preferences({
            "entity": {
                "default_tab": "history",
            },
            "visibility": {
                "entity": {
                    "overview": True,
                    "history": False,
                    "relations": True,
                    "sources": True,
                },
            },
        })

        self.assertEqual(
            prefs["entity"]["default_tab"],
            "overview",
        )

    def test_entity_tab_config_returns_visible_tabs_and_default(self):
        prefs = _validate_ui_preferences({
            "entity": {
                "default_tab": "relations",
            },
            "visibility": {
                "entity": {
                    "overview": True,
                    "history": False,
                    "relations": True,
                    "sources": False,
                },
            },
        })

        visible, default_tab = _entity_tab_config(prefs)

        self.assertEqual(
            visible,
            {
                "overview": True,
                "history": False,
                "relations": True,
                "sources": False,
            },
        )
        self.assertEqual(
            default_tab,
            "relations",
        )

    def test_entity_source_rows_deduplicate_references(self):
        rows = _entity_source_rows({
            "current": [
                {"source_uri": "fixture://source/a"},
            ],
            "events": [
                {"source_uri": "fixture://source/a"},
            ],
            "history": [
                {"source_uri": "fixture://source/b"},
            ],
            "relations": [
                {"source_uri": "fixture://source/b"},
                {"source_uri": "fixture://source/b"},
            ],
        })

        self.assertEqual(len(rows), 2)

        by_uri = {
            row["source_uri"]: row
            for row in rows
        }

        self.assertEqual(
            by_uri["fixture://source/a"]["reference_count"],
            2,
        )
        self.assertIn(
            "現在",
            by_uri["fixture://source/a"]["used_by"],
        )
        self.assertIn(
            "Event",
            by_uri["fixture://source/a"]["used_by"],
        )

        self.assertEqual(
            by_uri["fixture://source/b"]["reference_count"],
            3,
        )
        self.assertIn(
            "履歴",
            by_uri["fixture://source/b"]["used_by"],
        )
        self.assertIn(
            "関連",
            by_uri["fixture://source/b"]["used_by"],
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

    def test_saved_completed_task_is_restored_as_read_only_history(self):
        result = core_task_selection_result({
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "request": "メインPCのGPUを比較して",
            "status": "completed",
            "phase": "completed",
            "selected_capability": "pkb_web_compare",
            "question": None,
            "effective_request": "メインPCのGPUを比較して",
            "comparison": {
                "status": "insufficient_evidence",
                "current": "DRV-G3",
                "latest": None,
            },
        })
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["read_only_history"])
        self.assertTrue(result["resumed_from_storage"])
        self.assertIn("完了済みTask", result["message"])
        self.assertEqual(
            result["selected_capability"],
            "pkb_web_compare",
        )
        self.assertEqual(
            result["comparison"]["status"],
            "insufficient_evidence",
        )
        self.assertIsNone(result["question"])

    def test_saved_task_selection_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            core_task_selection_result({
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "status": "mystery",
            })

    @patch("interfaces.web.app._pkb_repository.load_entity_detail")
    @patch("interfaces.web.app._pkb_repository.resolve_component_reference")
    @patch("interfaces.web.app._entity_map")
    def test_magi_pkb_request_resolves_component_and_returns_model(
        self, entity_map_mock, resolve_mock, detail_mock
    ):
        entity_map_mock.return_value = ENTITIES
        resolve_mock.return_value = {
            "id": ENTITIES["GPU1"]["id"],
            "name": "GPU1",
            "entity_type": "gpu",
            "relation_role": "primary_gpu",
            "parent_name": "メインPC",
            "role_token": "GPU",
        }
        detail_mock.return_value = {
            "entity": {
                "id": ENTITIES["GPU1"]["id"],
                "name": "GPU1",
                "domain": "pc",
                "entity_type": "gpu",
            },
            "current": [
                {
                    "predicate": "manufacturer",
                    "value": "AMD",
                    "semantic_kind": "attribute",
                    "valid_to": None,
                },
                {
                    "predicate": "model",
                    "value": "Radeon RX 9070 XT",
                    "semantic_kind": "attribute",
                    "valid_to": None,
                },
                {
                    "predicate": "current_driver",
                    "value": "26.9.1",
                    "semantic_kind": "state",
                    "valid_to": None,
                },
            ],
            "relations": [],
            "events": [],
        }
        result = _execute_magi_pkb_request(
            "メインPCのGPUの種類は？",
            {
                "source": "pkb",
                "request_ids": ["REQ-1"],
                "what": "メインPCに搭載されているGPUの種類（モデル）",
            },
        )
        self.assertEqual(result["operation"], "entity_detail")
        self.assertEqual(result["result"]["result_kind"], "entity_detail")
        self.assertIn("AMD Radeon RX 9070 XT", result["answer"])
        self.assertIn("26.9.1", result["answer"])
        resolve_mock.assert_called_once()
        detail_mock.assert_called_once()

    @patch("interfaces.web.app._execute_core_read")
    @patch("interfaces.web.app._entity_map")
    def test_magi_pkb_request_unresolved_component_does_not_broaden_to_all_claims(
        self, entity_map_mock, core_read_mock
    ):
        entity_map_mock.return_value = {
            "サブPC": ENTITIES["サブPC"],
        }
        result = _execute_magi_pkb_request(
            "メインPCのGPUについて、今の最新ドライバーと比較して。",
            {
                "source": "pkb",
                "request_ids": ["REQ-1"],
                "what": "メインPCに搭載されているGPUの種類（モデル）",
            },
        )
        self.assertEqual(result["operation"], "target_resolution")
        self.assertEqual(result["total"], 0)
        self.assertEqual(
            result["result"]["result_kind"],
            "pkb_target_unresolved",
        )
        self.assertEqual(
            result["result"]["reason"],
            "parent_computer_not_unique_or_missing",
        )
        self.assertIn("GPUの対象PCを一意に確認できませんでした", result["answer"])
        core_read_mock.assert_not_called()

    @patch("interfaces.web.app._execute_core_read")
    @patch("interfaces.web.app._pkb_repository.resolve_component_reference")
    @patch("interfaces.web.app._entity_map")
    def test_magi_pkb_request_missing_component_relation_does_not_broaden(
        self, entity_map_mock, resolve_mock, core_read_mock
    ):
        entity_map_mock.return_value = ENTITIES
        resolve_mock.return_value = None
        result = _execute_magi_pkb_request(
            "メインPCのGPUについて教えて。",
            {
                "source": "pkb",
                "request_ids": ["REQ-1"],
                "what": "メインPCのGPUモデル",
            },
        )
        self.assertEqual(result["operation"], "target_resolution")
        self.assertEqual(
            result["result"]["reason"],
            "component_relation_not_unique_or_missing",
        )
        self.assertIn("メインPCのGPUを一意に確認できませんでした", result["answer"])
        core_read_mock.assert_not_called()

    @patch("interfaces.web.app._execute_core_read")
    @patch("interfaces.web.app._entity_map")
    def test_magi_pkb_request_exposes_only_scoped_public_os_terms(
        self, entity_map_mock, core_read_mock
    ):
        entity_map_mock.return_value = ENTITIES
        core_read_mock.return_value = {
            "capability": "pkb_search",
            "result": {
                "status": "ok",
                "result_kind": "claims",
                "total": 2,
                "items": [
                    {
                        "entity_name": "サブPC",
                        "predicate": "os_release_changed",
                        "value": {
                            "product": "Windows 11",
                            "to_release": "26H2",
                            "transition_hint": "upgrade",
                        },
                    },
                    {
                        "entity_name": "サブPC",
                        "predicate": "current_os_release",
                        "value": "26H2",
                    },
                ],
            },
            "answer": "PKBの記録では、サブPCの現在OSは26H2です。",
            "total": 2,
            "tool": "pkb",
            "operation": "search",
            "status": "ok",
            "confidentiality": "private",
        }
        result = _execute_magi_pkb_request(
            "サブPCの現在のOSと、そのバージョンの最新既知不具合を比較して。",
            {
                "source": "pkb",
                "request_ids": ["REQ-1"],
                "what": "サブPCの現在のOSバージョン",
            },
        )
        self.assertEqual(
            result["source_metadata"]["public_query_terms"],
            ["Windows 11", "26H2"],
        )

    @patch("interfaces.web.app._pkb_repository.list_components")
    @patch("interfaces.web.app._pkb_repository.load_entity_detail")
    def test_cooperative_pkb_probe_expands_known_pc_to_component_overview(
        self, detail_mock, components_mock
    ):
        detail_mock.return_value = {
            "entity": {
                "id": ENTITIES["メインPC"]["id"],
                "name": "メインPC",
                "domain": "pc",
                "entity_type": "computer",
            },
            "current": [
                {
                    "id": UUID("66666666-6666-6666-6666-666666666666"),
                    "predicate": "firmware",
                    "value": "FW-1",
                    "valid_from": __import__("datetime").datetime(2026, 9, 1, tzinfo=__import__("datetime").timezone.utc),
                }
            ],
            "relations": [
                {
                    "id": UUID("77777777-7777-7777-7777-777777777777"),
                    "predicate": "has_component",
                    "valid_to": None,
                    "valid_from": __import__("datetime").datetime(2026, 9, 1, tzinfo=__import__("datetime").timezone.utc),
                }
            ],
            "events": [
                {
                    "id": UUID("88888888-8888-8888-8888-888888888888"),
                    "predicate": "updated",
                    "recorded_at": __import__("datetime").datetime(2026, 9, 2, tzinfo=__import__("datetime").timezone.utc),
                }
            ],
        }
        components_mock.return_value = [
            {
                "component_id": UUID(ENTITIES["GPU1"]["id"]),
                "component_name": "GPU1",
                "component_type": "gpu",
                "relation_role": "primary_gpu",
                "current_driver": "DRV-G3",
                "state_source_uri": "fixture://gpu",
            },
            {
                "component_id": UUID(ENTITIES["NIC1"]["id"]),
                "component_name": "NIC1",
                "component_type": "network_adapter",
                "relation_role": "wired_nic",
                "current_driver": None,
                "state_source_uri": None,
            },
        ]
        result = _execute_cooperative_local_probe(
            "pkb_search",
            "メインPCについて調べて",
            {
                "matched_entities": [
                    {
                        "id": ENTITIES["メインPC"]["id"],
                        "name": "メインPC",
                        "domain": "pc",
                        "entity_type": "computer",
                    }
                ]
            },
        )
        self.assertEqual(result["operation"], "entity_overview")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["result"]["result_kind"], "components")
        self.assertEqual(result["result"]["probe_kind"], "entity_overview")
        self.assertIn("GPU1", result["answer"])
        self.assertIn("DRV-G3", result["answer"])
        self.assertIsInstance(result["result"]["current"][0]["id"], str)
        self.assertIsInstance(result["result"]["current"][0]["valid_from"], str)
        self.assertIsInstance(result["result"]["relations"][0]["valid_from"], str)
        self.assertIsInstance(result["result"]["events"][0]["recorded_at"], str)
        detail_mock.assert_called_once_with(ENTITIES["メインPC"]["id"])
        components_mock.assert_called_once_with(
            UUID(ENTITIES["メインPC"]["id"])
        )

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

    def test_web_core_answer_requires_primary_source_for_latest_value(self):
        answer = web_core_answer({
            "hits": [{"title": "AMD Drivers", "url": "https://www.amd.com"}],
            "fact_summary": {
                "kind": "driver_version",
                "status": "primary_source_no_current_candidate",
                "preferred_kind": "adrenalin_version",
                "best_candidate": None,
                "primary_domains": ["www.amd.com"],
                "groups": [
                    {
                        "kind": "adrenalin_version",
                        "status": "single_candidate",
                        "best_candidate": "26.6.4",
                        "candidates": [
                            {
                                "value": "26.6.4",
                                "primary_source_count": 0,
                            }
                        ],
                    },
                    {
                        "kind": "driver_version",
                        "status": "single_candidate",
                        "best_candidate": "26.9.1",
                        "candidates": [
                            {
                                "value": "26.9.1",
                                "primary_source_count": 0,
                            }
                        ],
                    },
                ],
                "historical_groups": [],
            },
        })
        self.assertIn("www.amd.com", answer)
        self.assertIn("確定しません", answer)
        self.assertIn("26.6.4", answer)
        self.assertIn("26.9.1", answer)

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

    def test_finance_core_answer_empty_result_keeps_requested_period(self):
        answer = finance_core_answer({
            "total": 0,
            "transaction_count": 0,
            "requested_start_date": "2026-09-01",
            "requested_end_date": "2026-09-30",
            "data_start_date": None,
            "data_end_date": None,
            "income_total": 0,
            "expense_total": 0,
            "net_total": 0,
        })
        self.assertIn("2026-09-01〜2026-09-30", answer)
        self.assertIn("明細が見つかりませんでした", answer)

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
