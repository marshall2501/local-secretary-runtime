"""Isolated acceptance checks; synthetic tasks only, no live DB or models."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import asyncio
import inspect
import unittest
from nicegui import Client, core, ui
from uuid import UUID

from pkb_proto import daily_pkb as daily
from pkb_proto.core_advisor import AdvisorResult


class CoreGuiTests(TestCase):
    def test_preferences_roundtrip_validation_and_live_apply(self):
        old = deepcopy(daily._UI_PREFERENCES)
        try:
            for value in range(1, 11):
                with TemporaryDirectory() as directory:
                    path = Path(directory) / 'prefs.json'
                    prefs = daily._default_ui_preferences()
                    prefs['core'].update(open_limit=value, completed_limit=value)
                    daily.save_ui_preferences(prefs, path)
                    daily._apply_ui_preferences(daily.load_ui_preferences(path))
                    self.assertEqual(daily._UI_PREFERENCES['core']['open_limit'], value)
                    self.assertEqual(daily._UI_PREFERENCES['core']['completed_limit'], value)
                    self.assertEqual(set(daily._CORE_UI_OPEN), set(daily.CORE_UI_DEFAULT_OPEN))
            for invalid in (True, False, '4', '10', 4.0, 0, -1, 11, 20, 50, None):
                prefs = daily._validate_ui_preferences({'core': {'open_limit': invalid, 'completed_limit': 10}})
                self.assertEqual(prefs['core']['open_limit'], 5)
                self.assertEqual(prefs['core']['completed_limit'], 10)
                prefs = daily._validate_ui_preferences({'core': {'open_limit': 4, 'completed_limit': invalid}})
                self.assertEqual(prefs['core']['open_limit'], 4)
                self.assertEqual(prefs['core']['completed_limit'], 5)
        finally:
            daily._apply_ui_preferences(old)

    def test_independent_windows_reach_beyond_fifty_and_stop_at_end(self):
        waiting = [{'id': f'waiting-{i}'} for i in range(73)]
        completed = [{'id': f'completed-{i}'} for i in range(8)]
        def loader(data):
            def read(limit, offset):
                self.assertLessEqual(limit, 50)
                return data[offset:offset + limit]
            return read
        rows, more = daily.load_core_task_window(loader(waiting), 5)
        self.assertEqual(len(rows), 5)
        self.assertTrue(more)
        done, more = daily.load_core_task_window(loader(completed), 5)
        self.assertEqual(len(done), 5)
        self.assertTrue(more)
        rows, more = daily.load_core_task_window(loader(waiting), 75)
        self.assertEqual(rows, waiting)
        self.assertFalse(more)
        done, more = daily.load_core_task_window(loader(completed), 10)
        self.assertEqual(done, completed)
        self.assertFalse(more)
        self.assertEqual(daily.load_core_task_window(loader([]), 5), ([], False))

    def test_sql_paging_filters_offsets_and_validation(self):
        for name in ('open', 'completed', 'recent'):
            loader = getattr(daily, f'load_{name}_core_tasks')
            with patch.object(daily, 'connection') as connection:
                cur = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
                cur.fetchall.return_value = []
                self.assertEqual(loader(limit=6, offset=60), [])
                sql, params = cur.execute.call_args.args
                self.assertIn('LIMIT %s OFFSET %s', sql)
                self.assertEqual(params, (6, 60))
                self.assertIn("daily_read_only_v1", sql)
                if name == 'open':
                    self.assertIn("'waiting_external', 'running'", sql)
                elif name == 'completed':
                    self.assertIn("t.status='completed'", sql)
                else:
                    self.assertNotIn('AND t.status', sql)
            for offset in (-1, True, '5'):
                with self.assertRaises(ValueError):
                    loader(offset=offset)

    def test_legacy_final_export_prefers_persisted_task_and_keeps_synthesis(self):
        advisor = {
            'cycles': [{'cycle': 1, 'synthesis': {'next_step': 'observe'}},
                       {'cycle': 2, 'synthesis': {'next_step': 'observe', 'selected_capability': 'pkb_search'}}],
            'cooperative_execution': {'action_id': 'a', 'result_id': 'r', 'capability': 'pkb_search',
                                      'final_next_step': 'respond', 'final_reason': 'repeat_probe_stopped_with_evidence'},
        }
        original = deepcopy(advisor)
        trace = {'task': {'status': 'completed', 'phase': 'completed', 'question': None,
                         'magi_baseline': {'status': 'question', 'selected_capability': None},
                         'observation_pack': {'version': 'magi_observation_v2', 'task_observations': [
                             {'cycle': 1, 'evidence_preview': [{'component_name': 'NIC1', 'current_driver': None}]}]}}}
        payload = daily._advisor_log_export({'status': 'waiting_external', 'question': 'old'}, advisor, trace)
        self.assertEqual(payload['core']['status'], 'completed')
        self.assertIsNone(payload['core']['question'])
        self.assertEqual(payload['final_core_decision']['next_step'], 'respond')
        self.assertEqual(payload['final_core_decision']['task_status'], 'completed')
        self.assertEqual(payload['magi']['cycles'][1]['synthesis']['next_step'], 'observe')
        self.assertIsNone(payload['magi']['cycles'][0]['melchior']['selected_capability'])
        self.assertEqual(payload['magi']['cycles'][0]['result']['result_id'], 'r')
        self.assertEqual(advisor, original)
        self.assertIsNone(daily.core_magi_presentation({}, {'synthesis': {'next_step': 'observe'}}, {})['final_core_decision'])

    def test_repeat_proposal_acceptance_worker_and_export(self):
        proposal = AdvisorResult('ok', 'mismatch', next_step='observe', proposed_action='pkb_web_compare', reason='Compare evidence')
        execution = {'capability': 'pkb_search', 'total': 2, 'answer': 'GPU1 / DRV-G3; NIC1 / no driver',
                     'result': {'result_kind': 'components', 'items': [
                         {'component_name': 'GPU1', 'current_driver': 'DRV-G3'},
                         {'component_name': 'NIC1', 'current_driver': None}]}}
        baseline = {'status': 'question', 'selected_capability': None}
        with patch.object(daily, 'list_advisor_models', return_value=[]), \
             patch.object(daily, 'choose_advisor_model', return_value='fixture'), \
             patch.object(daily, 'advise_core', side_effect=[proposal, proposal]) as advise, \
             patch.object(daily, '_write_core_advisor_shadow', return_value=True) as write, \
             patch.object(daily, '_claim_cooperative_probe', return_value=True), \
             patch.object(daily, '_execute_cooperative_local_probe', return_value=execution) as execute, \
             patch.object(daily, '_record_cooperative_probe', return_value=('action-fixture', 'result-fixture')), \
             patch.object(daily, '_finalize_cooperative_probe') as finalize:
            daily._run_core_advisor_shadow(UUID(int=1), 'メインPCについて調べて', baseline,
                                           {'version': 'magi_observation_v1'}, 'fixture', 60)
        execute.assert_called_once()
        self.assertEqual(advise.call_count, 2)
        final = write.call_args_list[-1].args[1]
        self.assertEqual(final['job_status'], 'completed')
        self.assertEqual(final['cycles'][1]['synthesis']['next_step'], 'observe')
        self.assertEqual(final['cycles'][1]['casper']['proposed_action'], 'pkb_web_compare')
        self.assertEqual(final['final_core_decision']['reason'], 'repeat_probe_stopped_with_evidence')
        self.assertEqual(final['final_core_decision']['task_status'], 'completed')
        pack = final['observation_pack_after_action']
        self.assertEqual(pack['task_progress']['cycle'], 2)
        self.assertEqual(pack['task_progress']['executed_capabilities'], ['pkb_search'])
        self.assertEqual(final['cycles'][0]['result']['evidence_preview'][0]['current_driver'], 'DRV-G3')
        self.assertIsNone(final['cycles'][0]['result']['evidence_preview'][1]['current_driver'])
        payload = daily._advisor_log_export({}, final, {'task': {'status': 'completed', 'observation_pack': pack}})
        self.assertEqual(payload['final_core_decision']['next_step'], 'respond')
        self.assertEqual(len(payload['magi']['cycles']), 2)
        finalize.assert_called_once()

    def test_final_decision_is_persisted_with_task_status(self):
        with patch.object(daily, 'connection') as connection:
            cur = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = ({'existing': 'preserved'},)
            daily._finalize_cooperative_probe(UUID(int=1),
                {'next_step': 'clarify', 'reason': 'second_cycle_clarify'}, {}, {'capability': 'pkb_search'})
            update = next(c for c in cur.execute.call_args_list if 'UPDATE secretary.tasks' in c.args[0])
            checkpoint = update.args[1][1].obj
            self.assertEqual(checkpoint['existing'], 'preserved')
            self.assertEqual(checkpoint['final_core_decision']['task_status'], 'waiting_external')
            self.assertEqual(checkpoint['final_core_decision']['next_step'], 'clarify')


class CoreGuiPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old = deepcopy(daily._UI_PREFERENCES)
        daily._apply_ui_preferences(daily._default_ui_preferences())
        self.waiting = [self.item(i, 'waiting_external') for i in range(14)]
        self.done = [self.item(i + 100, 'completed') for i in range(12)]
        def read(items):
            return lambda limit=20, offset=0: items[offset:offset+limit]
        self.patches = [patch.object(core, 'loop', asyncio.get_running_loop()),
                        patch.object(daily, 'list_advisor_models', return_value=[]),
                        patch.object(daily, 'load_open_core_tasks', side_effect=read(self.waiting)),
                        patch.object(daily, 'load_completed_core_tasks', side_effect=read(self.done)),
                        patch.object(daily, 'load_recent_core_tasks', side_effect=read(self.waiting + self.done)),
                        patch.object(daily, 'load_core_task_trace', side_effect=lambda task_id:
                            {'task': next(x for x in self.waiting + self.done if x['id'] == str(task_id)), 'actions': []})]
        for p in self.patches:
            p.start()
        self.client = Client(ui.page('/core-gui-test'))

    async def asyncTearDown(self):
        self.client.delete()
        for p in reversed(self.patches):
            p.stop()
        daily._apply_ui_preferences(self.old)

    def item(self, i, status):
        return {'id': str(UUID(int=i+1)), 'status': status, 'request': f'fixture-{i}',
                'revision': 1, 'phase': 'completed' if status == 'completed' else 'awaiting_clarification',
                'action_count': 0, 'result_count': 0, 'updated_at': 'fixture'}

    def elements(self, text):
        return [e for e in self.client.elements.values() if getattr(e, 'text', '') == text]

    async def click(self, text, index=0):
        element = self.elements(text)[index]
        handler = next(v.handler for v in element._event_listeners.values() if v.type == 'click')
        callback = inspect.getclosurevars(handler).nonlocals['callback']
        result = callback()
        if inspect.isawaitable(result):
            await result
        for _ in range(4):
            await asyncio.sleep(0)

    async def test_protocol_slots_and_legacy_flow_layout(self):
        with self.client, patch.object(
            daily, 'list_magi_models', return_value=['qwen3.5:9b', 'gemma3:12b']
        ), patch.object(daily, 'choose_magi_model', return_value='qwen3.5:9b'):
            daily.core_page()
            self.assertTrue(self.elements('MAGI member configuration'))
            self.assertTrue(self.elements('MELCHIOR'))
            self.assertTrue(self.elements('BALTHASAR'))
            self.assertTrue(self.elements('CASPER'))
            controls = {
                label: next(e for e in self.client.elements.values()
                            if e._props.get('label') == label)
                for label in ('MELCHIOR / model', 'BALTHASAR / model', 'CASPER / model')
            }
            self.assertEqual(controls['MELCHIOR / model'].value, 'qwen3.5:9b')
            self.assertNotIn('disable', controls['MELCHIOR / model']._props)
            for label in ('BALTHASAR / model', 'CASPER / model'):
                self.assertIsNone(controls[label].value)
                self.assertIn('disable', controls[label]._props)
            protocol_button = self.elements('MELCHIORへ分析依頼')[0]
            legacy = next(e for e in self.client.elements.values()
                          if isinstance(e, ui.expansion)
                          and e._props.get('label') == '旧MAGI v0・現行経路（回帰用）')
            self.assertFalse(legacy.value)
            def within_legacy(element):
                while element is not None:
                    if element is legacy:
                        return True
                    parent_slot = getattr(element, 'parent_slot', None)
                    element = parent_slot.parent if parent_slot else None
                return False
            legacy_model = next(e for e in self.client.elements.values()
                                if e._props.get('label') == 'Legacy CASPER Advisor Model')
            self.assertTrue(within_legacy(legacy_model))
            self.assertTrue(within_legacy(self.elements('OODA')[0]))
            self.assertTrue(within_legacy(self.elements('この縦断でまだ行わないこと')[0]))
            old_flow = next(e for e in self.client.elements.values()
                            if isinstance(e, ui.expansion)
                            and e._props.get('label') == '旧MAGI v0 処理フロー（回帰用）')
            self.assertTrue(within_legacy(old_flow))
            self.assertFalse(old_flow.value)
            elements = list(self.client.elements.values())
            self.assertGreater(elements.index(legacy), elements.index(protocol_button))
            self.assertTrue(self.elements('既存Task（旧経路）'))

    async def test_protocol_invalid_json_shows_diagnostics_and_copy_controls(self):
        probe = {
            'status': 'invalid',
            'assignment': {'member': 'MELCHIOR', 'provider': 'ollama', 'model': 'qwen3.5:9b'},
            'response': None,
            'request_envelope': {'protocol_version': '1.0', 'user_input': {'raw': 'test'}},
            'validation_errors': ['invalid_json'],
            'diagnostic': {'raw_length': 0, 'thinking_length': 131,
                           'done_reason': 'length', 'eval_count': 1800},
            'legacy_router_used': False,
            'pkb_read_executed': False,
        }
        async def fake_bound(*args, **kwargs):
            return probe
        with self.client, patch.object(
            daily, 'list_magi_models', return_value=['qwen3.5:9b']
        ), patch.object(daily, 'choose_magi_model', return_value='qwen3.5:9b'), \
             patch.object(daily.run, 'io_bound', side_effect=fake_bound):
            daily.core_page()
            await self.click('MELCHIORへ分析依頼')
            self.assertTrue(self.elements('Envelopeをコピー'))
            self.assertTrue(self.elements('診断情報をコピー'))
            self.assertTrue(self.elements('LLM応答診断（返答本文・Thinking本文は非表示）'))
            self.assertFalse(self.elements('analysis_resultをコピー'))
            self.assertTrue(any(
                '"done_reason": "length"' in getattr(e, 'content', '')
                for e in self.client.elements.values()
            ))

    async def test_two_initial_windows_load_more_keeps_selection_and_other_section(self):
        with self.client:
            daily.core_page()
            self.assertEqual(len(self.elements('開く')), 10)
            self.assertEqual(len(self.elements('さらに読み込む')), 2)
            await self.click('開く')
            self.assertTrue(self.elements('同じTaskを再開'))
            await self.click('さらに読み込む')
            self.assertEqual(len(self.elements('開く')), 15)
            self.assertTrue(self.elements('同じTaskを再開'))
            self.assertTrue(self.elements('fixture-104'))
            self.assertFalse(self.elements('fixture-105'))
            # Lists use the drawer's normal scroll, with no independent scroll areas.
            sections = [e for e in self.client.elements.values()
                        if 'overflow-y-auto' in e._classes]
            self.assertEqual(len(sections), 0)
            self.assertTrue(self.elements('Task履歴を見る'))

    async def test_history_has_second_page_and_opens_task_by_link(self):
        with self.client:
            daily.core_history_page()
            self.assertEqual(len(self.elements('Taskを開く')), 20)
            await self.click('次へ')
            self.assertEqual(len(self.elements('Taskを開く')), 6)
            await self.click('前へ')
            self.assertEqual(len(self.elements('Taskを開く')), 20)

    async def test_four_rows_independent_growth_and_live_preference_update(self):
        prefs = daily._default_ui_preferences()
        prefs['core'].update(open_limit=4, completed_limit=4)
        daily._apply_ui_preferences(prefs)
        with self.client, patch.object(daily.ui, 'timer') as timers:
            daily.core_page()
            self.assertEqual(len(self.elements('開く')), 8)
            await self.click('開く', 3)
            await self.click('さらに読み込む', 0)
            self.assertEqual(len(self.elements('開く')), 12)
            self.assertTrue(self.elements('fixture-7'))
            self.assertFalse(self.elements('fixture-104'))
            # Refreshing the first section changes element insertion order;
            # locate the completed section's callback by its closure.
            for element in self.elements('さらに読み込む'):
                listener = next(v.handler for v in element._event_listeners.values() if v.type == 'click')
                callback = inspect.getclosurevars(listener).nonlocals['callback']
                if 'completed_limit' in callback.__code__.co_consts:
                    callback()
                    break
            else:
                self.fail('Completed load-more callback missing')
            for _ in range(4):
                await asyncio.sleep(0)
            self.assertEqual(len(self.elements('開く')), 16)
            self.assertTrue(self.elements('fixture-107'))
            self.assertFalse(self.elements('fixture-108'))
            self.assertTrue(self.elements('同じTaskを再開'))
            prefs['core'].update(open_limit=1, completed_limit=4)
            daily._apply_ui_preferences(prefs)
            sync = next(call.args[1] for call in timers.call_args_list
                        if call.args[1].__name__ == 'sync_task_preferences')
            sync()
            for _ in range(4):
                await asyncio.sleep(0)
            self.assertEqual(len(self.elements('開く')), 9)
            self.assertTrue(self.elements('同じTaskを再開'))
            self.assertFalse(any('overflow-y-auto' in e._classes for e in self.client.elements.values()))

    async def test_task_count_settings_are_separate_from_limits_toggle(self):
        with self.client:
            daily.settings_page()
            def card_for(element):
                while not isinstance(element, ui.card):
                    element = element.parent_slot.parent
                return element
            counts = self.elements('RITSUKO — Task表示件数')[0]
            limits = self.elements('この縦断でまだ行わないこと')[0]
            self.assertIsNot(card_for(counts), card_for(limits))
            for label in ('進行中・確認待ち 初期表示件数', '完了済み 初期表示件数'):
                control = next(e for e in self.client.elements.values() if e._props.get('label') == label)
                self.assertIs(card_for(control), card_for(counts))
            limit_row = limits.parent_slot.parent
            self.assertEqual(len([e for e in limit_row if isinstance(e, ui.switch)]), 1)
            self.assertFalse(any(isinstance(e, ui.select) for e in limit_row))

    async def test_settings_controls_save_both_counts_without_restart(self):
        with self.client, TemporaryDirectory() as directory, patch.object(
            daily, '_preferences_path', return_value=Path(directory) / 'prefs.json'
        ):
            daily.settings_page()
            labels = {'進行中・確認待ち 初期表示件数': 4, '完了済み 初期表示件数': 4}
            for label, value in labels.items():
                control = next(e for e in self.client.elements.values() if e._props.get('label') == label)
                self.assertEqual(control.options, list(range(1, 11)))
                control.value = value
            await self.click('保存して反映')
            self.assertEqual(daily._UI_PREFERENCES['core']['open_limit'], 4)
            self.assertEqual(daily._UI_PREFERENCES['core']['completed_limit'], 4)
            self.assertEqual(daily.load_ui_preferences()['core']['completed_limit'], 4)

    async def test_query_selection_and_final_decision_rendered_separately(self):
        self.done[0]['advisor_shadow'] = {
            'job_status': 'completed', 'cycles': [
                {'cycle': 1, 'melchior': {'status': 'question'}, 'synthesis': {'next_step': 'observe'},
                 'action': {'capability': 'pkb_search'}, 'result': {'result_count': 2}},
                {'cycle': 2, 'casper': {'proposed_action': 'pkb_web_compare'}, 'synthesis': {'next_step': 'observe'}}],
            'final_core_decision': {'next_step': 'respond', 'reason': 'repeat_probe_stopped_with_evidence', 'task_status': 'completed'}}
        with self.client:
            daily.core_page(task_id=self.done[0]['id'])
            for _ in range(4):
                await asyncio.sleep(0)
            for label in ('Cycle 1', 'Cycle 2', 'FINAL CORE DECISION', 'next_step = respond',
                          'reason = repeat_probe_stopped_with_evidence', 'task_status = completed', 'ログをコピー'):
                self.assertTrue(self.elements(label), label)
            self.assertFalse(self.elements('同じTaskを再開'))
