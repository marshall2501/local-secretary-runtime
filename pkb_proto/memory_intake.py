"""Input-wide atomic Memory Write Service for fictional isolated PKB only."""
from datetime import datetime
from uuid import UUID
from psycopg.types.json import Jsonb
from .entity_model_service import _allowed, advance_state_for_event, classify_predicate
from .ingestion_gate import memory_write_decision
from .memory_extractor import extract
from .memory_grounding import ground, load_catalog
from .memory_registry import EFFECT_RULES
from .pending_service import enqueue_candidate


def write_intake(db, intake, *, extractor=extract):
    intake.validate()
    if not _allowed(db):
        raise ValueError('Refusing non-isolated DB or non-dedicated writer')
    fingerprint = intake.payload_hash()
    with db.transaction(), db.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (intake.input_id,))
        cur.execute('SELECT payload_hash,result FROM secretary.pkb_memory_intakes WHERE input_id=%s', (UUID(intake.input_id),))
        previous = cur.fetchone()
        if previous:
            if previous[0] != fingerprint:
                raise ValueError('input_id_payload_mismatch')
            return {**previous[1], 'status': 'replayed'}
        candidates = extractor(intake)
        if not isinstance(candidates, list) or len(candidates) > 100:
            raise ValueError('invalid_candidate_batch')
        identifiers = [c.get('candidate_id') if isinstance(c, dict) else None for c in candidates]
        if any(not isinstance(i, str) or not i or len(i) > 80 for i in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError('invalid_or_duplicate_candidate_id')
        catalog = load_catalog(cur)
        prepared = []
        for candidate in candidates:
            grounded = ground(intake, candidate, catalog)
            decision, reason = memory_write_decision(intake, candidate, grounded)
            prepared.append([candidate, grounded, decision, reason])
        # Also serialize different input IDs that update the same entity.
        for entity in sorted({g['entity_id'] for _, g, d, _ in prepared if d == 'auto_commit'}):
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,1))', (entity,))
            cur.execute('SELECT id FROM secretary.entities WHERE id=%s AND retired_at IS NULL', (entity,))
            if cur.fetchone() is None:
                raise ValueError('entity_changed_during_grounding')
        prospective = {}
        for item in prepared:
            c, g, decision, _ = item
            rule = EFFECT_RULES.get(g.get('predicate'))
            if decision != 'auto_commit' or not rule or c['content_class'] != 'fact' or c['modality'] != 'asserted':
                continue
            key = (g['entity_id'], rule.state_predicate)
            when = datetime.fromisoformat(g['time']['resolved'])
            cur.execute("SELECT valid_from FROM secretary.claims WHERE entity_id=%s AND predicate=%s AND semantic_kind='state' AND valid_to IS NULL AND retracted_at IS NULL FOR UPDATE", key)
            row = cur.fetchone()
            prior = prospective.get(key, row[0] if row else None)
            if prior is not None and prior >= when:
                item[2:] = ['pending', 'state_time_not_monotonic']
            else:
                prospective[key] = when
        recorded = datetime.fromisoformat(intake.recorded_at)
        cur.execute('''INSERT INTO secretary.sources
          (source_type,uri,citation,retrieved_at,recorded_at,confidentiality,metadata)
          VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
          (intake.source_kind, intake.source_ref, intake.raw_text,
           datetime.fromisoformat(intake.observed_at) if intake.observed_at else recorded,
           recorded, intake.confidentiality,
           Jsonb(dict(fictional_only=True, input_id=intake.input_id, original_text=intake.raw_text,
                      contract_version=intake.contract_version, timezone=intake.timezone))))
        source_id = cur.fetchone()[0]
        results = []
        for c, g, decision, reason in prepared:
            result = dict(candidate_id=c['candidate_id'], decision=decision, status=g['validation'],
                          reason=reason, claim_id=None, pending_id=None, derived_claim_ids=[])
            if decision == 'auto_commit':
                factual = c['content_class'] in {'fact', 'observation'} and c['modality'] == 'asserted'
                semantic = classify_predicate(g['predicate']) if factual else None
                metadata = dict(content_class=c['content_class'], modality=c['modality'], polarity=c['polarity'],
                                basis=c['basis'], provenance='explicit', time=g['time'],
                                input_id=intake.input_id, candidate_id=c['candidate_id'])
                when = datetime.fromisoformat(g['time']['resolved'])
                cur.execute('''INSERT INTO secretary.claims
                  (entity_id,source_id,claim_type,semantic_kind,predicate,value,evidence,origin,
                   verification_status,valid_from,recorded_at,memory_metadata)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,'user_explicit','unverified',%s,%s,%s) RETURNING id''',
                  (g['entity_id'], source_id, ('observation' if c['content_class']=='observation' else 'fact') if factual else 'unconfirmed',
                   semantic, g['predicate'], Jsonb(g['value']), c['evidence']['quote'], when, recorded, Jsonb(metadata)))
                result['claim_id'] = str(cur.fetchone()[0])
                if factual and c['content_class'] == 'fact':
                    derived = advance_state_for_event(cur, entity_id=UUID(g['entity_id']), source_id=source_id,
                               event_predicate=g['predicate'], value=g['value'], evidence=c['evidence']['quote'],
                               valid_from=when, recorded_at=recorded)
                    if derived:
                        cur.execute('UPDATE secretary.claims SET memory_metadata=%s WHERE id=%s',
                                    (Jsonb({**metadata, 'provenance':'derived', 'derived_from':result['claim_id'],
                                            'effect_rule':g['predicate']}), derived))
                        result['derived_claim_ids'].append(derived)
            elif decision == 'pending':
                result['pending_id'] = enqueue_candidate(cur, input_id=intake.input_id, candidate_id=c['candidate_id'],
                    raw_text=c['evidence']['quote'], source_id=source_id, reason=reason, grounded=g, draft=c)
            result['audit'] = dict(draft=c, grounding=g)
            results.append(result)
        output = dict(status='committed', input_id=intake.input_id, source_id=str(source_id), candidates=results)
        cur.execute("INSERT INTO secretary.pkb_memory_intakes(input_id,payload_hash,source_id,status,result) VALUES (%s,%s,%s,'committed',%s)",
                    (UUID(intake.input_id), fingerprint, source_id, Jsonb(output)))
        for r in results:
            cur.execute('''INSERT INTO secretary.pkb_memory_candidate_receipts
               (input_id,candidate_id,decision,status,claim_id,pending_id,derived_claim_ids,audit)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
               (UUID(intake.input_id), r['candidate_id'], r['decision'], r['status'], r['claim_id'],
                r['pending_id'], [UUID(i) for i in r['derived_claim_ids']], Jsonb(r['audit'])))
        return output
