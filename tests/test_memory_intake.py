import copy
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
import unittest
from pkb_proto.memory_contracts import MemoryIntake, validate_draft
from pkb_proto.memory_extractor import extract
from pkb_proto.memory_grounding import ground, resolve_entity
from pkb_proto.ingestion_gate import memory_write_decision
from pkb_proto.memory_intake import write_intake
from pkb_proto.memory_registry import EFFECT_RULES

CATALOG = [dict(id='1', names={'サブPC','PC'}, kind='computer', retired=False),
           dict(id='2', names={'メインPC','PC'}, kind='computer', retired=False),
           dict(id='3', names={'NIKKE'}, kind='software', retired=False)]
NOW = datetime.fromisoformat('2026-09-30T12:00:00+09:00')


class MemoryIntakeTests(unittest.TestCase):
    def prepare(self, text):
        intake = MemoryIntake.issue(text, now=NOW)
        candidates = extract(intake)
        return intake, candidates, [ground(intake, c, CATALOG) for c in candidates]

    def test_os_event_value_and_policy(self):
        i, cs, gs = self.prepare('サブPCのWindows11を26H2に上げた。')
        self.assertEqual(gs[0]['value'], dict(product='Windows 11',to_release='26H2',transition_hint='upgrade'))
        self.assertEqual(memory_write_decision(i,cs[0],gs[0])[0], 'auto_commit')

    def test_ambiguous_pc(self):
        i, cs, gs = self.prepare('PCを26H2に上げた。')
        self.assertEqual(memory_write_decision(i,cs[0],gs[0]), ('pending','ambiguous'))

    def test_plan_not_fact(self):
        i, cs, gs = self.prepare('サブPCを26H2にする予定。')
        self.assertEqual((cs[0]['content_class'],cs[0]['modality']), ('plan','intended'))
        self.assertEqual(memory_write_decision(i,cs[0],gs[0])[0], 'auto_commit')

    def test_temporal_sequence_not_causality(self):
        i, cs, gs = self.prepare('サブPCを26H2にしてからNIKKEが重くなった。')
        self.assertEqual(len(cs),2)
        self.assertEqual([g['predicate'] for g in gs], ['os_release_changed','performance_observed'])
        self.assertEqual([memory_write_decision(i,c,g)[0] for c,g in zip(cs,gs)], ['auto_commit','auto_commit'])

    def test_mixed_candidates(self):
        i, cs, gs = self.prepare('サブPCを26H2に上げた。PCのGPUドライバーも更新した。')
        self.assertEqual([memory_write_decision(i,c,g)[0] for c,g in zip(cs,gs)], ['auto_commit','pending'])

    def test_yesterday_timezone_precision(self):
        _,_,gs = self.prepare('昨日サブPCを26H2に上げた。')
        self.assertEqual(gs[0]['time']['resolved'], '2026-09-29T00:00:00+09:00')
        self.assertEqual(gs[0]['time']['precision'], 'day')

    def test_time_prefix_not_discarded(self):
        i,cs,gs = self.prepare('昨日、サブPCを26H2に上げた。')
        self.assertEqual(gs[0]['time']['resolved'],'2026-09-29T00:00:00+09:00')
        self.assertEqual(memory_write_decision(i,cs[0],gs[0])[0],'auto_commit')
        i,cs,gs = self.prepare('明日、サブPCを26H2に上げた。')
        self.assertEqual(memory_write_decision(i,cs[0],gs[0])[0],'pending')

    def test_retired_and_unresolved(self):
        self.assertEqual(resolve_entity('old',[dict(names={'old'},retired=True)])[0],'invalid')
        self.assertEqual(resolve_entity('unknown',CATALOG)[0],'unresolved')

    def test_forbidden_fields_and_forged_evidence(self):
        i,cs,_ = self.prepare('サブPCを26H2に上げた。')
        for field in ('entity_id','canonical_predicate','verified','decision','derived_state'):
            c = {**cs[0],field:'forged'}
            self.assertEqual(validate_draft(c,i.raw_text),'invalid_draft_fields')
        c = copy.deepcopy(cs[0]); c['evidence']['start'] = True
        self.assertEqual(validate_draft(c,i.raw_text),'invalid_evidence')

    def test_model_labels_cannot_turn_uncertainty_into_fact(self):
        i, cs, _ = self.prepare('サブPCを26H2に上げたかもしれない。')
        cs[0]['modality']='asserted'
        self.assertEqual(memory_write_decision(i,cs[0],ground(i,cs[0],CATALOG))[0],'pending')

    def test_negative_and_question_pending(self):
        for text in ('サブPCを26H2に上げた？','サブPCを26H2にしたことはない。'):
            i,cs,gs = self.prepare(text)
            self.assertEqual(memory_write_decision(i,cs[0],gs[0])[0],'pending')

    def test_clipped_quote_cannot_hide_negation(self):
        i=MemoryIntake.issue('サブPCを26H2に上げたわけではない。')
        short=MemoryIntake.issue('サブPCを26H2に上げた')
        candidate=extract(short)[0]
        self.assertEqual(memory_write_decision(i,candidate,ground(i,candidate,CATALOG))[0],'pending')

    def test_correction_restricted_inference_and_task_only(self):
        i,cs,gs = self.prepare('サブPCを26H2に上げた。')
        c=cs[0]
        self.assertEqual(memory_write_decision(replace(i,confidentiality='restricted'),c,gs[0])[0],'pending')
        for key,value in [('basis','model_inference'),('operation_hint','correct'),('semantic_kind_hint','state')]:
            self.assertEqual(memory_write_decision(i,{**c,key:value},gs[0])[0],'pending')
        self.assertEqual(memory_write_decision(i,{**c,'retention_hint':'task_only'},gs[0])[0],'task_context_only')

    def test_greeting_zero_candidates(self):
        self.assertEqual(extract(MemoryIntake.issue('こんにちは。')),[])

    def test_effects_compatible(self):
        self.assertEqual(EFFECT_RULES['os_release_changed'].select({'to_release':'26H2'}),'26H2')
        self.assertEqual(EFFECT_RULES['driver_updated'].select('DRV-G3'),'DRV-G3')
        self.assertEqual(EFFECT_RULES['servo_updated'].select('SERVO-X3'),'SERVO-X3')

    def test_payload_hash_and_input_id(self):
        i=MemoryIntake.issue('サブPCを26H2に上げた。',now=NOW)
        i.validate()
        self.assertEqual(i.payload_hash(),replace(i).payload_hash())
        self.assertNotEqual(i.payload_hash(),replace(i,raw_text='変更').payload_hash())

    def test_production_refused_before_connection(self):
        db=SimpleNamespace(info=SimpleNamespace(dbname='secretary',host='localhost',user='postgres'))
        with self.assertRaisesRegex(ValueError,'Refusing'):
            write_intake(db,MemoryIntake.issue('サブPCを26H2に上げた。'))

    def test_gui_retains_envelope_for_retry(self):
        from pathlib import Path
        source=(Path(__file__).parents[1]/'interfaces/web/app.py').read_text(encoding='utf-8')
        self.assertIn('envelope.raw_text != text', source)
        self.assertIn('run.io_bound(register_memory_intake, envelope)', source)
        self.assertIn("@app.post('/api/pkb/intakes/issue')",source)


if __name__ == '__main__':
    unittest.main()
