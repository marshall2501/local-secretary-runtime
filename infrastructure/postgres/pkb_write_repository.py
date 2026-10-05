"""PostgreSQL adapter for the single-claim PKB write slice."""
from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb

from config.runtime_database import (
    allowed_daily_connection,
    connection_mode,
    source_ref_allowed,
)
from pkb.entity_model_service import classify_predicate
from pkb.ingestion_gate import InputRecord, ProposedClaim, Route, assess
from pkb.write_service import WriteResult, literal_gate, payload_hash
from infrastructure.postgres.pkb_entity_repository import (
    advance_state_for_event,
    authoritative_entity_aliases,
)


def write_one(db, record: InputRecord, claim: ProposedClaim) -> WriteResult:
    if not allowed_daily_connection(db):
        raise ValueError("Refusing non-prototype or non-local PostgreSQL connection")
    if not source_ref_allowed(db, record.source_ref):
        return WriteResult("rejected", "fictional_fixture_only")

    fingerprint = payload_hash(record, claim)
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (record.input_id,),
            )
            cur.execute(
                """SELECT payload_sha256, source_id, claim_id
                   FROM secretary.pkb_input_receipts WHERE input_id=%s""",
                (record.input_id,),
            )
            existing = cur.fetchone()
            if existing:
                digest, source_id, claim_id = existing
                if digest != fingerprint:
                    return WriteResult("rejected", "input_id_reused_with_different_payload")
                return WriteResult(
                    "replayed", "same_input_already_written",
                    str(claim_id), str(source_id),
                )

            aliases = authoritative_entity_aliases(cur)
            outcome = assess(record, claim, aliases=aliases)
            if outcome.route is not Route.AUTO_CANDIDATE:
                return WriteResult(
                    "rejected" if outcome.route is Route.REJECT else "review",
                    outcome.reason,
                )
            literal_error = literal_gate(record, claim)
            if literal_error:
                return WriteResult("review", literal_error)

            semantic_kind = classify_predicate(claim.predicate)
            if semantic_kind != "event":
                cur.execute(
                    """SELECT id FROM secretary.claims
                       WHERE entity_id=%s AND predicate=%s
                         AND retracted_at IS NULL
                       LIMIT 1""",
                    (UUID(claim.entity_key), claim.predicate),
                )
                if cur.fetchone():
                    return WriteResult(
                        "review", "existing_claim_requires_conflict_resolution"
                    )

            cur.execute(
                """INSERT INTO secretary.sources
                   (source_type, uri, citation, retrieved_at, recorded_at,
                    confidentiality, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    record.source_kind, record.source_ref, record.input_id,
                    record.recorded_at, record.recorded_at,
                    record.confidentiality,
                    Jsonb({
                        **(
                            {"fictional_only": True}
                            if connection_mode(db) == "isolated"
                            else {}
                        ),
                        "input_id": record.input_id,
                        "original_text": record.text,
                    }),
                ),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO secretary.claims
                   (entity_id, source_id, claim_type, semantic_kind,
                    predicate, value, evidence, origin, verification_status,
                    valid_from, recorded_at)
                   VALUES (%s,%s,'fact',%s,%s,%s,%s,'user_explicit','unverified',%s,%s)
                   RETURNING id""",
                (
                    UUID(claim.entity_key), source_id,
                    semantic_kind, claim.predicate, Jsonb(claim.value),
                    claim.evidence_quote, record.occurred_at, record.recorded_at,
                ),
            )
            claim_id = cur.fetchone()[0]
            advance_state_for_event(
                cur,
                entity_id=UUID(claim.entity_key),
                source_id=source_id,
                event_predicate=claim.predicate,
                value=claim.value,
                evidence=claim.evidence_quote,
                valid_from=record.occurred_at,
                recorded_at=record.recorded_at,
            )
            cur.execute(
                """INSERT INTO secretary.pkb_input_receipts
                   (input_id, payload_sha256, source_id, claim_id)
                   VALUES (%s,%s,%s,%s)""",
                (record.input_id, fingerprint, source_id, claim_id),
            )
            return WriteResult(
                "inserted",
                (
                    "fictional_single_claim_committed"
                    if connection_mode(db) == "isolated"
                    else "single_claim_committed"
                ),
                str(claim_id), str(source_id),
            )
