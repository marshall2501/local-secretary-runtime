"""Single-claim, fictional-only PostgreSQL write slice for the self-built PKB.

This is NOT a production memory writer or an LLM semantic verifier.
Use only a dedicated secretary_pkb_proto_* DB with the 001-004 schema and the
pkb_proto/sql/005_pkb_proto_receipts.sql prototype extension.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from .ingestion_gate import InputRecord, ProposedClaim, Route, assess

KNOWN_PREDICATES = {
    "driver_updated": "更新した",
    "servo_updated": "交換した",
}
UNCERTAIN = ("かもしれない", "未確認", "不明", "ではなく", "訂正", "らしい")


@dataclass(frozen=True)
class WriteResult:
    status: str  # inserted | replayed | review | rejected
    reason: str
    claim_id: str | None = None
    source_id: str | None = None


def payload_hash(record: InputRecord, claim: ProposedClaim) -> str:
    payload = {
        "input_id": record.input_id,
        "source_kind": record.source_kind,
        "source_ref": record.source_ref,
        "text": record.text,
        "recorded_at": record.recorded_at.isoformat(),
        "occurred_at": record.occurred_at.isoformat(),
        "confidentiality": record.confidentiality,
        "proposal": {
            "entity_key": claim.entity_key,
            "entity_mention": claim.entity_mention,
            "predicate": claim.predicate,
            "value": claim.value,
            "evidence_start": claim.evidence_start,
            "evidence_end": claim.evidence_end,
            "evidence_quote": claim.evidence_quote,
            "intent": claim.intent,
            "corrects_claim_id": claim.corrects_claim_id,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _literal_gate(record: InputRecord, claim: ProposedClaim) -> str | None:
    """Conservative *additional* gate, not general Japanese semantic validation."""
    if claim.predicate not in KNOWN_PREDICATES:
        return "predicate_not_supported_by_first_slice"
    quote = claim.evidence_quote
    if claim.value not in quote or KNOWN_PREDICATES[claim.predicate] not in quote:
        return "value_or_action_not_explicit_in_quote"
    if any(marker in quote for marker in UNCERTAIN):
        return "uncertain_or_correction_language"
    return None


def write_one(db, record: InputRecord, claim: ProposedClaim) -> WriteResult:
    """Atomic initial write. DB connector is injected by the isolated test harness.

    The DB login must be separately provisioned with restricted memory-write
    privileges. The live DB name is rejected *before any write*. This method
    handles one claim per input for the first prototype, not full episode
    extraction, correction, or conflict resolution.
    """
    info = db.info
    dbname = info.dbname or ""
    host = info.host or ""
    if not dbname.startswith("secretary_pkb_proto_") or host not in (
        "localhost", "127.0.0.1", "::1",
    ):
        raise ValueError("Refusing non-prototype or non-local PostgreSQL connection")
    if (info.user or "") != "secretary_pkb_proto_writer_20260927":
        raise ValueError("Refusing a DB login other than the dedicated PKB prototype writer")
    if not record.source_ref.startswith("fixture://"):
        return WriteResult("rejected", "fictional_fixture_only")

    fingerprint = payload_hash(record, claim)
    with db.transaction():
        with db.cursor() as cur:
            # Serialize retries with the same input ID. The PRIMARY KEY remains
            # the final cross-process guard; hash collisions only add contention.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (record.input_id,),
            )
            # The advisory transaction lock already serializes this input ID.
            # FOR UPDATE would unnecessarily require UPDATE privilege on the
            # restricted prototype writer; SELECT is sufficient here.
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
                return WriteResult("replayed", "same_input_already_written",
                                   str(claim_id), str(source_id))

            # Use authoritative entity IDs/names, never LLM-created identifiers.
            cur.execute(
                "SELECT id, name FROM secretary.entities WHERE retired_at IS NULL"
            )
            aliases = {str(entity_id): {name} for entity_id, name in cur.fetchall()}
            outcome = assess(record, claim, aliases=aliases)
            if outcome.route is not Route.AUTO_CANDIDATE:
                return WriteResult(
                    "rejected" if outcome.route is Route.REJECT else "review",
                    outcome.reason,
                )
            literal_error = _literal_gate(record, claim)
            if literal_error:
                return WriteResult("review", literal_error)

            # This first slice intentionally does not infer whether an existing
            # attribute is a replacement or an additional historical event.
            cur.execute(
                """SELECT id FROM secretary.claims
                   WHERE entity_id=%s AND predicate=%s
                     AND retracted_at IS NULL
                   LIMIT 1""",
                (UUID(claim.entity_key), claim.predicate),
            )
            if cur.fetchone():
                return WriteResult("review", "existing_claim_requires_conflict_resolution")

            from psycopg.types.json import Jsonb

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
                        "fictional_only": True, "input_id": record.input_id,
                        "original_text": record.text,
                    }),
                ),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO secretary.claims
                   (entity_id, source_id, claim_type, predicate, value, evidence,
                    origin, verification_status, valid_from, recorded_at)
                   VALUES (%s,%s,'fact',%s,%s,%s,'user_explicit','unverified',%s,%s)
                   RETURNING id""",
                (
                    UUID(claim.entity_key), source_id,
                    claim.predicate, Jsonb(claim.value),
                    claim.evidence_quote, record.occurred_at, record.recorded_at,
                ),
            )
            claim_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO secretary.pkb_input_receipts
                   (input_id, payload_sha256, source_id, claim_id)
                   VALUES (%s,%s,%s,%s)""",
                (record.input_id, fingerprint, source_id, claim_id),
            )
            return WriteResult("inserted", "fictional_single_claim_committed",
                               str(claim_id), str(source_id))
