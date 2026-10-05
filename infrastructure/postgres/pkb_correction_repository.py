"""PostgreSQL adapter for PKB correction persistence."""
from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from config.runtime_database import allowed_daily_connection, source_ref_allowed
from pkb.correction_service import (
    CorrectionResult,
    explicit_reassignment_quote,
    fingerprint,
)
from pkb.entity_model_service import classify_predicate
from pkb.ingestion_gate import InputRecord, ProposedClaim, Route, assess


def find_correction_targets(
    db,
    *,
    old_entity_id: str,
    predicate: str,
    value: str,
) -> list[tuple]:
    with db.cursor() as cur:
        cur.execute(
            """SELECT c.id, c.valid_from
               FROM secretary.claims c
               JOIN secretary.sources s ON s.id=c.source_id
               WHERE c.entity_id=%s AND c.predicate=%s
                 AND c.value=%s::jsonb AND c.origin='user_explicit'
                 AND c.verification_status='unverified'
                 AND c.retracted_at IS NULL
                 AND (s.uri LIKE 'fixture://daily-pkb/%%'
                      OR s.uri LIKE 'local://daily-pkb/%%')
               ORDER BY c.recorded_at DESC""",
            (UUID(old_entity_id), predicate, '"' + value + '"'),
        )
        return list(cur.fetchall())


def correct_entity(db, record: InputRecord, claim: ProposedClaim) -> CorrectionResult:
    if not allowed_daily_connection(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if not source_ref_allowed(db, record.source_ref):
        return CorrectionResult("rejected", "fictional_fixture_only")
    if claim.intent != "correction" or not claim.corrects_claim_id:
        return CorrectionResult(
            "rejected", "explicit_correction_and_old_claim_required"
        )
    try:
        old_id = UUID(claim.corrects_claim_id)
        new_entity_id = UUID(claim.entity_key)
    except (ValueError, AttributeError):
        return CorrectionResult("rejected", "invalid_claim_or_entity_uuid")
    payload_fingerprint = fingerprint(record, claim)

    with db.transaction(), db.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", (record.input_id,)
        )
        cur.execute(
            """SELECT payload_sha256, old_claim_id, new_claim_id
               FROM secretary.pkb_correction_receipts WHERE input_id=%s""",
            (record.input_id,),
        )
        existing = cur.fetchone()
        if existing:
            digest, prior_id, new_id = existing
            if digest != payload_fingerprint:
                return CorrectionResult(
                    "rejected", "input_id_reused_with_different_payload"
                )
            return CorrectionResult(
                "replayed", "correction_already_applied",
                str(prior_id), str(new_id),
            )

        cur.execute(
            """SELECT c.entity_id, e.name, c.predicate, c.value, c.valid_from,
                      c.recorded_at, c.verification_status, c.retracted_at, s.uri,
                      s.source_type
               FROM secretary.claims c
               JOIN secretary.entities e ON e.id=c.entity_id
               JOIN secretary.sources s ON s.id=c.source_id
               WHERE c.id=%s AND c.origin='user_explicit'""",
            (old_id,),
        )
        old = cur.fetchone()
        if old is None:
            return CorrectionResult("review", "original_user_claim_not_found")
        (
            old_entity_id, old_name, predicate, value, valid_from,
            old_recorded, verification, retracted, old_uri, old_source_type,
        ) = old
        if (
            not old_uri.startswith("fixture://")
            or old_source_type != "user_statement"
            or retracted is not None
            or verification != "unverified"
        ):
            return CorrectionResult("review", "original_not_eligible")
        if (
            record.source_kind != "user_statement"
            or record.confidentiality == "restricted"
        ):
            return CorrectionResult(
                "review", "correction_source_not_low_risk_user_statement"
            )
        if (
            new_entity_id == old_entity_id
            or claim.predicate != predicate
            or claim.value != value
        ):
            return CorrectionResult("review", "not_entity_only_correction")
        if record.recorded_at < old_recorded or record.occurred_at != valid_from:
            return CorrectionResult("review", "correction_time_mismatch")

        cur.execute("SELECT id, name FROM secretary.entities WHERE retired_at IS NULL")
        aliases = {str(entity_id): {name} for entity_id, name in cur.fetchall()}
        gate = assess(
            record, claim, aliases=aliases, known_claim_ids={str(old_id)}
        )
        if gate.route is Route.REJECT:
            return CorrectionResult("rejected", gate.reason)
        if gate.reason != "correction_needs_transactional_handling":
            return CorrectionResult("review", gate.reason)
        if not explicit_reassignment_quote(
            claim.evidence_quote, old_name, claim.entity_mention, claim.value
        ):
            return CorrectionResult(
                "review", "no_explicit_old_to_new_entity_reassignment"
            )
        cur.execute(
            """SELECT id FROM secretary.claims
               WHERE entity_id=%s AND predicate=%s
                 AND valid_from=%s
                 AND verification_status != 'retracted'
                 AND retracted_at IS NULL LIMIT 1""",
            (new_entity_id, predicate, valid_from),
        )
        if cur.fetchone():
            return CorrectionResult("review", "target_entity_has_existing_claim")
        cur.execute(
            """UPDATE secretary.claims
               SET verification_status='retracted', retracted_at=%s
               WHERE id=%s AND verification_status='unverified'
                 AND retracted_at IS NULL
               RETURNING id""",
            (record.recorded_at, old_id),
        )
        if cur.fetchone() is None:
            return CorrectionResult(
                "review", "original_was_corrected_concurrently"
            )
        cur.execute(
            """INSERT INTO secretary.sources
               (source_type, uri, citation, retrieved_at, recorded_at,
                confidentiality, metadata)
               VALUES ('user_statement',%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                record.source_ref, record.input_id, record.recorded_at,
                record.recorded_at, record.confidentiality,
                Jsonb({
                    "fictional_only": True,
                    "input_id": record.input_id,
                    "original_text": record.text,
                    "corrects_claim_id": str(old_id),
                }),
            ),
        )
        source_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO secretary.claims
               (entity_id, source_id, claim_type, semantic_kind,
                predicate, value, evidence, origin, verification_status,
                valid_from, recorded_at, supersedes_id)
               VALUES (%s,%s,'fact',%s,%s,%s,%s,'user_explicit','unverified',%s,%s,%s)
               RETURNING id""",
            (
                new_entity_id, source_id, classify_predicate(predicate),
                predicate, Jsonb(value), claim.evidence_quote,
                valid_from, record.recorded_at, old_id,
            ),
        )
        new_claim_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO secretary.pkb_correction_receipts
               (input_id, payload_sha256, old_claim_id, new_claim_id, source_id)
               VALUES (%s,%s,%s,%s,%s)""",
            (
                record.input_id, payload_fingerprint, old_id,
                new_claim_id, source_id,
            ),
        )
        return CorrectionResult(
            "corrected", "explicit_fixture_entity_reassigned",
            str(old_id), str(new_claim_id),
        )
