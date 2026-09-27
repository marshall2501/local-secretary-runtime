"""Fictional-only entity correction, preserving original Claim and Source.

This prototype accepts only an explicit correction of one existing user-stated
entity attribution. It does not infer correction targets, resolve general
contradictions, or establish external truth.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb

from .ingestion_gate import InputRecord, ProposedClaim, Route, assess

WRITER = "secretary_pkb_proto_writer_20260927"


@dataclass(frozen=True)
class CorrectionResult:
    status: str
    reason: str
    old_claim_id: str | None = None
    new_claim_id: str | None = None


def _fingerprint(record: InputRecord, claim: ProposedClaim) -> str:
    data = {
        "input_id": record.input_id,
        "source_kind": record.source_kind,
        "source_ref": record.source_ref,
        "text": record.text,
        "recorded_at": record.recorded_at.isoformat(),
        "occurred_at": record.occurred_at.isoformat(),
        "confidentiality": record.confidentiality,
        "entity_key": claim.entity_key,
        "entity_mention": claim.entity_mention,
        "predicate": claim.predicate,
        "value": claim.value,
        "start": claim.evidence_start,
        "end": claim.evidence_end,
        "quote": claim.evidence_quote,
        "intent": claim.intent,
        "old": claim.corrects_claim_id,
    }
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def explicit_reassignment_quote(quote: str, old_name: str, new_name: str, value: str) -> bool:
    """Conservative fixture-specific grammar; never general Japanese semantics."""
    return (
        "訂正" in quote
        and old_name != new_name
        and old_name + "ではなく" + new_name in quote
        and value in quote
    )


def correct_entity(db, record: InputRecord, claim: ProposedClaim) -> CorrectionResult:
    """One explicit correction inside a transaction on an isolated fixture DB."""
    info = db.info
    if (
        (info.dbname or "") != "secretary_pkb_proto_20260927"
        or (info.host or "") not in ("localhost", "127.0.0.1", "::1")
        or (info.user or "") != WRITER
    ):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if not record.source_ref.startswith("fixture://"):
        return CorrectionResult("rejected", "fictional_fixture_only")
    if claim.intent != "correction" or not claim.corrects_claim_id:
        return CorrectionResult("rejected", "explicit_correction_and_old_claim_required")
    try:
        old_id = UUID(claim.corrects_claim_id)
        new_entity_id = UUID(claim.entity_key)
    except (ValueError, AttributeError):
        return CorrectionResult("rejected", "invalid_claim_or_entity_uuid")
    fingerprint = _fingerprint(record, claim)

    with db.transaction(), db.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (record.input_id,))
        cur.execute(
            """SELECT payload_sha256, old_claim_id, new_claim_id
               FROM secretary.pkb_correction_receipts WHERE input_id=%s""",
            (record.input_id,),
        )
        existing = cur.fetchone()
        if existing:
            digest, prior_id, new_id = existing
            if digest != fingerprint:
                return CorrectionResult("rejected", "input_id_reused_with_different_payload")
            return CorrectionResult("replayed", "correction_already_applied",
                                    str(prior_id), str(new_id))

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
        (old_entity_id, old_name, predicate, value, valid_from,
         old_recorded, verification, retracted, old_uri, old_source_type) = old
        if (
            not old_uri.startswith("fixture://")
            or old_source_type != "user_statement"
            or retracted is not None
            or verification != "unverified"
        ):
            return CorrectionResult("review", "original_not_eligible")
        if record.source_kind != "user_statement" or record.confidentiality == "restricted":
            return CorrectionResult("review", "correction_source_not_low_risk_user_statement")
        if new_entity_id == old_entity_id or claim.predicate != predicate or claim.value != value:
            return CorrectionResult("review", "not_entity_only_correction")
        if record.recorded_at < old_recorded or record.occurred_at != valid_from:
            return CorrectionResult("review", "correction_time_mismatch")

        cur.execute(
            "SELECT id, name FROM secretary.entities WHERE retired_at IS NULL"
        )
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
            return CorrectionResult("review", "no_explicit_old_to_new_entity_reassignment")
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
        # Atomic conditional update: another correction of the same old Claim
        # cannot silently create a second accepted successor.
        cur.execute(
            """UPDATE secretary.claims
               SET verification_status='retracted', retracted_at=%s
               WHERE id=%s AND verification_status='unverified'
                 AND retracted_at IS NULL
               RETURNING id""",
            (record.recorded_at, old_id),
        )
        if cur.fetchone() is None:
            return CorrectionResult("review", "original_was_corrected_concurrently")

        cur.execute(
            """INSERT INTO secretary.sources
               (source_type, uri, citation, retrieved_at, recorded_at,
                confidentiality, metadata)
               VALUES ('user_statement',%s,%s,%s,%s,%s,%s)
               RETURNING id""",
            (
                record.source_ref, record.input_id, record.recorded_at,
                record.recorded_at, record.confidentiality,
                Jsonb({"fictional_only": True, "input_id": record.input_id,
                       "original_text": record.text, "corrects_claim_id": str(old_id)}),
            ),
        )
        source_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO secretary.claims
               (entity_id, source_id, claim_type, predicate, value, evidence,
                origin, verification_status, valid_from, recorded_at, supersedes_id)
               VALUES (%s,%s,'fact',%s,%s,%s,'user_explicit','unverified',%s,%s,%s)
               RETURNING id""",
            (
                new_entity_id, source_id, predicate, Jsonb(value),
                claim.evidence_quote, valid_from, record.recorded_at, old_id,
            ),
        )
        new_claim_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO secretary.pkb_correction_receipts
               (input_id, payload_sha256, old_claim_id, new_claim_id, source_id)
               VALUES (%s,%s,%s,%s,%s)""",
            (record.input_id, fingerprint, old_id, new_claim_id, source_id),
        )
        return CorrectionResult("corrected", "explicit_fixture_entity_reassigned",
                                str(old_id), str(new_claim_id))
