"""Pure single-claim PKB write rules and persistence-port delegate."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .ingestion_gate import InputRecord, ProposedClaim

# Event predicates describe changes. Attributes require labeled values.
ATTRIBUTE_LABELS = {
    "model": ("機種", "型番", "モデル"),
    "manufacturer": ("メーカー", "製造元"),
}
KNOWN_PREDICATES = {
    "driver_updated": ("更新した", "更新しておいた", "アップデートした"),
    "servo_updated": ("交換した", "取り替えた"),
    **ATTRIBUTE_LABELS,
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
    if claim.predicate in ATTRIBUTE_LABELS:
        # 属性名に値が直結しているときだけ候補にする。無関係な値の流用は拒否。
        labels = "|".join(re.escape(v) for v in ATTRIBUTE_LABELS[claim.predicate])
        pattern = rf"(?:{labels})\s*(?:は|が|:|：)\s*{re.escape(claim.value)}(?=$|[\s。、，,;；!?！？）)」]|です)"
        if not re.search(pattern, quote):
            return "value_or_attribute_not_explicit_in_quote"
        if len(re.findall(rf"(?:{labels})\s*(?:は|が|:|：)", quote)) != 1:
            return "ambiguous_attribute_statement"
    elif claim.value not in quote or not any(
        phrase in quote for phrase in KNOWN_PREDICATES[claim.predicate]
    ):
        return "value_or_action_not_explicit_in_quote"
    if any(marker in record.text or marker in quote for marker in UNCERTAIN):
        return "uncertain_or_correction_language"
    return None


def normalize_explicit_attribute_evidence(text: str, arguments: dict) -> dict:
    """Ground an LLM's imprecise attribute quote in one exact original phrase.

    This is positional reconciliation, NOT a new interpretation of the text.
    Leave proposals unchanged unless their original span is in bounds, names
    the entity and attribute, and the complete entity-label-value phrase occurs
    exactly once in the author's original statement. The PKB repository still
    independently checks the result and all conflicts/permissions.
    """
    result = dict(arguments)
    predicate = result.get("predicate")
    if not isinstance(text, str) or predicate not in ATTRIBUTE_LABELS:
        return result
    mention, value = result.get("entity_mention"), result.get("value")
    start, end = result.get("evidence_start"), result.get("evidence_end")
    if (not isinstance(mention, str) or not mention.strip()
            or not isinstance(value, str) or not value.strip()
            or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(text)):
        return result
    original_quote = text[start:end]
    if mention not in original_quote:
        return result
    labels = "|".join(re.escape(label) for label in ATTRIBUTE_LABELS[predicate])
    label_pattern = rf"(?:{labels})\s*(?:は|が|:|：)"
    if not re.search(label_pattern, original_quote):
        return result
    # An already correct, fully quoted value is an immutable source of truth.
    # Do not rewrite accepted evidence offsets: existing Action receipts and
    # reconciliation payloads depend on exact original arguments.
    explicit_value = (
        rf"(?:{labels})\s*(?:は|が|:|：)\s*{re.escape(value)}"
        rf"(?=$|[\s。、，,;；!?！？）)」]|です)"
    )
    if (len(re.findall(label_pattern, original_quote)) == 1
            and re.search(explicit_value, original_quote)):
        return result
    # Multiple declarations of the same attribute are not a safe auto-fix.
    if len(re.findall(label_pattern, text)) != 1:
        return result
    if any(marker in text for marker in UNCERTAIN):
        return result
    # The narrow grammar intentionally refuses distant cross-sentence pairing.
    exact_phrase = (
        rf"{re.escape(mention)}(?:\s*の)?\s*(?:{labels})"
        rf"\s*(?:は|が|:|：)\s*{re.escape(value)}"
        rf"(?=$|[\s。、，,;；!?！？）)」]|です)"
    )
    matches = list(re.finditer(exact_phrase, text))
    if len(matches) != 1:
        return result
    match = matches[0]
    if (match.start(), match.end()) == (start, end):
        return result
    result["evidence_start"] = match.start()
    result["evidence_end"] = match.end()
    return result


# Backward-compatible internal name for existing tests/importers.
_literal_gate = literal_gate


def write_one(repository, record: InputRecord, claim: ProposedClaim) -> WriteResult:
    return repository.write_one(record, claim)
