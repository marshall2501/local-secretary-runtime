"""Pure PKB correction rules and persistence-port delegate."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .ingestion_gate import InputRecord, ProposedClaim


@dataclass(frozen=True)
class CorrectionResult:
    status: str
    reason: str
    old_claim_id: str | None = None
    new_claim_id: str | None = None


def fingerprint(record: InputRecord, claim: ProposedClaim) -> str:
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
        json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


_fingerprint = fingerprint


def explicit_reassignment_quote(
    quote: str, old_name: str, new_name: str, value: str
) -> bool:
    return (
        "訂正" in quote
        and old_name != new_name
        and old_name + "ではなく" + new_name in quote
        and value in quote
    )


def correct_entity(repository, record: InputRecord, claim: ProposedClaim) -> CorrectionResult:
    return repository.correct_entity(record, claim)
