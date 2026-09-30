"""Actual PostgreSQL acceptance tests. Only a fresh GitHub service DB is allowed."""
import os
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4
from unittest.mock import patch
import unittest
import psycopg
from psycopg import sql
from pkb_proto.memory_contracts import MemoryIntake
from pkb_proto.memory_intake import write_intake
from pkb_proto.ingestion_gate import InputRecord, ProposedClaim
from pkb_proto.write_service import write_one
from pkb_proto.pending_service import enqueue, accept_pending, list_pending
from pkb_proto.correction_service import correct_entity
from pkb_proto.query_service import query_claims, ClaimQuery

DBNAME='secretary_pkb_proto_20260927'
WRITER='secretary_pkb_proto_writer_20260927'
ROOT=Path(__file__).resolve().parents[1]
ENABLED=os.environ.get('PKB_MEMORY_INTEGRATION')=='1' and os.environ.get('GITHUB_ACTIONS')=='true'


@unittest.skipUnless(ENABLED, 'Requires fresh disposable GitHub Actions PostgreSQL service')
class MemoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin=psycopg.connect(host='127.0.0.1',port=5432,dbname=DBNAME,user='postgres',
                                password=os.environ['PKB_TEST_PASSWORD'],autocommit=True)
        if cls.admin.execute("SELECT to_regnamespace('secretary')").fetchone()[0] is not None:
            raise RuntimeError('Refusing non-empty database; this suite never resets existing installations')
        cls.admin.execute('CREATE SCHEMA secretary')
        cls.admin.execute('CREATE TABLE secretary.schema_migrations(version text PRIMARY KEY,sha256 text NOT NULL)')
        cls.admin.execute('CREATE ROLE secretary_memory_writer NOLOGIN')
        cls.admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(WRITER),sql.Literal(os.environ['PKB_TEST_PASSWORD'])))
        cls.admin.execute(next((ROOT/'db/migrations').glob('001*')).read_text(encoding='utf-8'))
        cls.admin.execute("INSERT INTO secretary.entities(name,entity_type,domain) VALUES ('メインPC','computer','pc'),('サブPC','computer','pc'),('NIKKE','software','game'),('SERVO1','rc_servo','rc')")
        for number in (5,6,8,9,10,11,12,13,14,15,19):
            path=next((ROOT/'pkb_proto/sql').glob(f'{number:03d}_*'))
            cls.admin.execute(path.read_text(encoding='utf-8'))
        cls.admin.execute(sql.SQL('GRANT USAGE ON SCHEMA secretary TO {}').format(sql.Identifier(WRITER)))
        cls.admin.execute(sql.SQL('GRANT SELECT ON secretary.entities,secretary.sources,secretary.claims,secretary.pending_claims TO {}').format(sql.Identifier(WRITER)))
        cls.admin.execute(sql.SQL('GRANT INSERT ON secretary.sources,secretary.claims TO {}').format(sql.Identifier(WRITER)))
        cls.admin.execute(sql.SQL('GRANT SELECT,INSERT ON secretary.pkb_input_receipts TO {}').format(sql.Identifier(WRITER)))

    @classmethod
    def tearDownClass(cls):
        cls.admin.close()

    def connect(self):
        return psycopg.connect(host='127.0.0.1',port=5432,dbname=DBNAME,user=WRITER,
                               password=os.environ['PKB_TEST_PASSWORD'],autocommit=True)

    def setUp(self):
        # Only rows in the fresh schema created by setUpClass; the service is discarded by CI.
        self.admin.execute('TRUNCATE secretary.claims,secretary.pkb_pending_intake,secretary.pkb_memory_intakes CASCADE')
        self.db=self.connect()
        self.addCleanup(self.db.close)

    def intake(self,text,seconds=0):
        return MemoryIntake.issue(text,now=datetime.now(timezone.utc)+timedelta(seconds=seconds))

    def counts(self):
        return tuple(self.admin.execute(sql.SQL('SELECT count(*) FROM secretary.{}').format(sql.Identifier(t))).fetchone()[0]
                     for t in ('sources','claims','pkb_pending_intake','pkb_memory_intakes','pkb_memory_candidate_receipts'))

    def test_os_event_state_and_replay_before_extraction(self):
        i=self.intake('サブPCのWindows11を26H2に上げた。')
        before=self.counts(); result=write_intake(self.db,i)
        self.assertEqual(result['candidates'][0]['decision'],'auto_commit')
        self.assertEqual(tuple(a-b for a,b in zip(self.counts(),before)),(1,2,0,1,1))
        rows=self.db.execute('SELECT predicate,value,memory_metadata FROM secretary.claims ORDER BY semantic_kind').fetchall()
        self.assertEqual(rows[0][0],'os_release_changed')
        self.assertEqual(rows[0][1]['product'],'Windows 11')
        self.assertEqual(rows[1][1],'26H2')
        self.assertEqual(rows[1][2]['provenance'],'derived')
        after=self.counts()
        replay=write_intake(self.db,i,extractor=lambda _:self.fail('re-extracted'))
        self.assertEqual(replay,{**result,'status':'replayed'})
        self.assertEqual(self.counts(),after)
        with self.assertRaisesRegex(ValueError,'payload_mismatch'):
            write_intake(self.db,replace(i,raw_text='変更'),extractor=lambda _:self.fail('re-extracted'))
        self.assertEqual(self.counts(),after)

    def test_new_input_id_same_implicit_event_is_deduplicated(self):
        first=self.intake('サブPCのWindows11を26H2に上げた。')
        original=write_intake(self.db,first)
        before=self.counts()
        second=self.intake('サブPCのWindows11を26H2に上げた。',1)
        repeated=write_intake(self.db,second)
        candidate=repeated['candidates'][0]
        self.assertEqual(candidate['decision'],'ignore')
        self.assertEqual(candidate['reason'],'duplicate_existing_event')
        self.assertEqual(candidate['duplicate_of_claim_id'],original['candidates'][0]['claim_id'])
        self.assertEqual(tuple(a-b for a,b in zip(self.counts(),before)),(1,0,0,1,1))
        self.assertEqual(self.db.execute("SELECT count(*) FROM secretary.claims WHERE predicate='os_release_changed'").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM secretary.claims WHERE predicate='current_os_release'").fetchone()[0],1)

    def test_same_target_can_be_recorded_again_after_state_changed(self):
        first=write_intake(self.db,self.intake('サブPCを26H2に上げた。'))
        self.assertEqual(first['candidates'][0]['decision'],'auto_commit')
        changed=write_intake(self.db,self.intake('サブPCを27H1に上げた。',1))
        self.assertEqual(changed['candidates'][0]['decision'],'auto_commit')
        repeated=write_intake(self.db,self.intake('サブPCを26H2に上げた。',2))
        self.assertEqual(repeated['candidates'][0]['decision'],'auto_commit')
        self.assertIsNone(repeated['candidates'][0]['duplicate_of_claim_id'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM secretary.claims WHERE predicate='os_release_changed'").fetchone()[0],3)
        self.assertEqual(self.db.execute("SELECT value FROM secretary.claims WHERE predicate='current_os_release' AND valid_to IS NULL").fetchone()[0],'26H2')

    def test_ambiguous_pending_and_replay(self):
        i=self.intake('PCを26H2に上げた。')
        result=write_intake(self.db,i)
        self.assertEqual(result['candidates'][0]['reason'],'ambiguous')
        counts=self.counts(); write_intake(self.db,i)
        self.assertEqual(self.counts(),counts)
        self.assertEqual(self.db.execute('SELECT count(*) FROM secretary.claims').fetchone()[0],0)
        self.assertEqual(len(list_pending(self.db)),1)

    def test_plan_preserves_current_state(self):
        write_intake(self.db,self.intake('サブPCを25H2に上げた。'))
        result=write_intake(self.db,self.intake('サブPCを26H2にする予定。',1))
        self.assertEqual(result['candidates'][0]['derived_claim_ids'],[])
        row=self.db.execute("SELECT claim_type,semantic_kind,memory_metadata FROM secretary.claims WHERE id=%s",(result['candidates'][0]['claim_id'],)).fetchone()
        self.assertEqual(row[:2],('unconfirmed',None))
        self.assertEqual(row[2]['modality'],'intended')
        self.assertEqual(self.db.execute("SELECT value FROM secretary.claims WHERE predicate='current_os_release' AND valid_to IS NULL").fetchone()[0],'25H2')

    def test_sequence_observation_without_cause(self):
        before=self.db.execute('SELECT count(*) FROM secretary.entity_relations').fetchone()[0]
        result=write_intake(self.db,self.intake('サブPCを26H2にしてからNIKKEが重くなった。'))
        self.assertEqual([c['decision'] for c in result['candidates']],['auto_commit','auto_commit'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM secretary.entity_relations').fetchone()[0],before)
        self.assertEqual(self.db.execute("SELECT claim_type FROM secretary.claims WHERE predicate='performance_observed'").fetchone()[0],'observation')

    def test_mixed_atomic_and_receipts(self):
        result=write_intake(self.db,self.intake('サブPCを26H2に上げた。PCのGPUドライバーも更新した。'))
        self.assertEqual([c['decision'] for c in result['candidates']],['auto_commit','pending'])
        row=self.db.execute('SELECT memory_context FROM secretary.pkb_pending_intake').fetchone()[0]
        self.assertEqual(row['source_id'],result['source_id'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM secretary.pkb_memory_candidate_receipts').fetchone()[0],2)

    def test_database_error_rolls_back_source_event_state_pending_receipts(self):
        original=self.counts()
        self.admin.execute("CREATE FUNCTION secretary.fail_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected failure'; END $$")
        self.admin.execute('CREATE TRIGGER fail_receipt BEFORE INSERT ON secretary.pkb_memory_candidate_receipts FOR EACH ROW EXECUTE FUNCTION secretary.fail_receipt()')
        try:
            with self.assertRaises(psycopg.Error):
                write_intake(self.db,self.intake('サブPCを26H2に上げた。PCを26H2に上げた。'))
            self.assertEqual(self.counts(),original)
        finally:
            self.admin.execute('DROP TRIGGER fail_receipt ON secretary.pkb_memory_candidate_receipts')
            self.admin.execute('DROP FUNCTION secretary.fail_receipt()')

    def test_concurrent_same_input_commits_once(self):
        i=self.intake('サブPCを26H2に上げた。')
        def submit(_):
            with self.connect() as db:
                return write_intake(db,i)['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit,range(2))),['committed','replayed'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM secretary.claims').fetchone()[0],2)

    def test_same_time_conflict_pending_and_state_succession(self):
        i=self.intake('サブPCを25H2に上げた。')
        write_intake(self.db,i)
        second=replace(self.intake('サブPCを26H2に上げた。'),recorded_at=i.recorded_at)
        result=write_intake(self.db,second)
        self.assertEqual(result['candidates'][0]['decision'],'pending')
        write_intake(self.db,self.intake('サブPCを26H2に上げた。',1))
        rows=self.db.execute("SELECT value,valid_to FROM secretary.claims WHERE predicate='current_os_release' ORDER BY valid_from").fetchall()
        self.assertIsNotNone(rows[0][1]); self.assertEqual(rows[1],('26H2',None))

    def test_legacy_driver_servo_pending_correction_and_query(self):
        now=datetime.now(timezone.utc)-timedelta(seconds=10)
        for name,predicate,value in [('GPU1','driver_updated','DRV-G3'),('SERVO1','servo_updated','SERVO-X3')]:
            entity=str(self.db.execute('SELECT id FROM secretary.entities WHERE name=%s',(name,)).fetchone()[0])
            text=f'{name}を{value}へ' + ('更新した。' if predicate=='driver_updated' else '交換した。')
            record=InputRecord(str(uuid4()),'user_statement','fixture://legacy',text,now,now)
            claim=ProposedClaim(entity,name,predicate,value,0,len(text),text)
            result=write_one(self.db,record,claim)
            self.assertEqual(result.status,'inserted')
            self.assertEqual(write_one(self.db,record,claim).status,'replayed')
        self.assertEqual(self.db.execute("SELECT count(*) FROM secretary.claims WHERE semantic_kind='state'").fetchone()[0],2)
        gpu=str(self.db.execute("SELECT id FROM secretary.entities WHERE name='GPU1'").fetchone()[0])
        pending=enqueue(self.db,input_id=str(uuid4()),raw_text='GPU1をDRV-G4へ更新した。',
                        reason='existing_claim_requires_conflict_resolution',entity_id=gpu,predicate='driver_updated',proposed_value='DRV-G4')
        self.assertEqual(accept_pending(self.db,pending.pending_id).status,'accepted')
        page=query_claims(self.db,ClaimQuery(entity_id=UUID(gpu),predicate='driver_updated',include_history=True))
        self.assertEqual(page.total,2)
        # Legacy correction targets a plain PC event, not a derived component state.
        ids={name:str(identifier) for identifier,name in self.db.execute("SELECT id,name FROM secretary.entities WHERE entity_type='computer'").fetchall()}
        text='サブPCをDRV-A1へ更新した。'
        old=write_one(self.db,InputRecord(str(uuid4()),'user_statement','fixture://legacy',text,now,now),
                      ProposedClaim(ids['サブPC'],'サブPC','driver_updated','DRV-A1',0,len(text),text))
        correction='訂正：サブPCではなくメインPCのDRV-A1'
        corrected=correct_entity(self.db,InputRecord(str(uuid4()),'user_statement','fixture://correction',correction,now,now),
                     ProposedClaim(ids['メインPC'],'メインPC','driver_updated','DRV-A1',0,len(correction),correction,'correction',old.claim_id))
        self.assertEqual(corrected.status,'corrected')


if __name__=='__main__':
    unittest.main()
