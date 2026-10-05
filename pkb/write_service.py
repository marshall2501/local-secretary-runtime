"""Pure single-claim PKB write rules and persistence-port delegate."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .ingestion_gate import InputRecord, ProposedClaim

KNOWN_PREDICATES = {
    "driver_updated": ("更新した", "更新しておいた", "アップデートした"),
    "servo_updated": ("交換した", "取り替えた"),
}
UNCERTAIN = ("かもしれない", "未確認", "不明", "ではなく", "訂正", "らしい")


@dataclass(frozen=True)
class WriteResult:
    status: str
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


def literal_gate(record: InputRecord, claim: ProposedClaim) -> str | None:
    """Conservative additional gate, not general Japanese semantic validation."""
    if claim.predicate not in KNOWN_PREDICATES:
        return "predicate_not_supported_by_first_slice"
    quote = claim.evidence_quote
    if claim.value not in quote or not any(
        phrase in quote for phrase in KNOWN_PREDICATES[claim.predicate]
    ):
        return "value_or_action_not_explicit_in_quote"
    if any(marker in quote for marker in UNCERTAIN):
        return "uncertain_or_correction_language"
    return None


# Backward-compatible internal name for existing tests/importers.
_literal_gate = literal_gate


def write_one(repository, record: InputRecord, claim: ProposedClaim) -> WriteResult:
    return repository.write_one(record, claim)
