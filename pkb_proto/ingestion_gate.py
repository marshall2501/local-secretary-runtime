"""PKB ingestion gate: fail closed before any authoritative DB write.

This module is deliberately storage- and LLM-independent. Passing this gate
means the model cited exact input text, NOT that its interpretation is true.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal


SourceKind = Literal["user_statement", "file", "web", "tool", "service"]
Intent = Literal["assertion", "correction"]


def memory_write_decision(intake, candidate, grounded):
    """v1 policy: model assertions never confer permission or factual status."""
    from .memory_extractor import literal_supported
    from .memory_registry import EFFECT_RULES
    if grounded['validation'] == 'invalid':
        return 'ignore', grounded['reason']
    if candidate['retention_hint'] == 'task_only':
        return 'task_context_only', 'not_durable'
    if candidate['operation_hint'] != 'assert':
        return 'pending', 'correction_target_requires_review'
    if intake.source_kind != 'user_statement' or candidate['basis'] != 'explicit_user_statement':
        return 'pending', 'non_user_or_inferred_requires_review'
    if intake.confidentiality == 'restricted':
        return 'pending', 'high_impact_or_restricted'
    if grounded['reason'] != 'resolved':
        return 'pending', grounded['reason']
    if not grounded['predicate'] or not literal_supported(candidate, intake):
        return 'pending', 'unsupported_or_uncertain_statement'
    if candidate['modality'] not in {'asserted', 'intended'} or candidate['polarity'] != 'positive':
        return 'pending', 'uncertain_or_negative'
    if candidate['semantic_kind_hint'] in {'state', 'relation'}:
        return 'pending', 'direct_state_or_relation_requires_review'
    rule = EFFECT_RULES.get(grounded['predicate'])
    if rule and (grounded['entity_type'] not in rule.entity_types or not candidate['object']['raw']):
        return 'pending', 'missing_value_or_incompatible_entity'
    return 'auto_commit', 'explicit_supported_statement'


class Route(str, Enum):
    AUTO_CANDIDATE = "auto_candidate"
    REVIEW = "review"
    REJECT = "reject"


@dataclass(frozen=True)
class InputRecord:
    input_id: str
    source_kind: SourceKind
    source_ref: str
    text: str
    recorded_at: datetime
    occurred_at: datetime
    confidentiality: Literal["public", "private", "restricted"] = "private"


@dataclass(frozen=True)
class ProposedClaim:
    entity_key: str
    entity_mention: str
    predicate: str
    value: str
    evidence_start: int
    evidence_end: int
    evidence_quote: str
    intent: Intent = "assertion"
    corrects_claim_id: str | None = None


@dataclass(frozen=True)
class GateResult:
    route: Route
    reason: str
    input_id: str
    evidence_quote: str | None = None


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def assess(
    record: InputRecord,
    claim: ProposedClaim,
    *,
    aliases: dict[str, set[str]],
    known_claim_ids: set[str] | None = None,
    high_impact: bool = False,
) -> GateResult:
    """Validate one proposal; this function never persists or promotes facts.

    The caller must supply aliases from the authoritative entity catalog,
    not from an LLM-generated guess. Unknown entities and corrections require
    explicit resolution. The DB service must separately check conflicts,
    idempotency, transaction isolation and authorization at commit time.
    """
    if not record.input_id or not record.source_ref or not record.text.strip():
        return GateResult(Route.REJECT, "missing_source_identity_or_text", record.input_id)
    if record.source_kind not in {"user_statement", "file", "web", "tool", "service"}:
        return GateResult(Route.REJECT, "invalid_source_kind", record.input_id)
    if not _aware(record.recorded_at) or not _aware(record.occurred_at):
        return GateResult(Route.REJECT, "timezone_required", record.input_id)
    if (not claim.predicate.strip() or not claim.value.strip()
            or claim.intent not in {"assertion", "correction"}):
        return GateResult(Route.REJECT, "invalid_claim_fields", record.input_id)
    if not (0 <= claim.evidence_start < claim.evidence_end <= len(record.text)):
        return GateResult(Route.REJECT, "invalid_evidence_span", record.input_id)
    quote = record.text[claim.evidence_start:claim.evidence_end]
    if quote != claim.evidence_quote:
        return GateResult(Route.REJECT, "evidence_not_in_original_text", record.input_id)
    if claim.entity_mention not in quote:
        return GateResult(Route.REVIEW, "entity_not_supported_by_quote", record.input_id, quote)
    if claim.entity_key not in aliases or claim.entity_mention not in aliases[claim.entity_key]:
        return GateResult(Route.REVIEW, "unknown_or_unresolved_entity", record.input_id, quote)
    # One mention mapped to two canonical entities is ambiguous, even if the
    # proposed entity was selected by the model.
    matches = [key for key, names in aliases.items() if claim.entity_mention in names]
    if len(matches) != 1:
        return GateResult(Route.REVIEW, "ambiguous_entity_alias", record.input_id, quote)
    if claim.intent == "correction":
        if not claim.corrects_claim_id or claim.corrects_claim_id not in (known_claim_ids or set()):
            return GateResult(Route.REVIEW, "correction_target_unresolved", record.input_id, quote)
        return GateResult(Route.REVIEW, "correction_needs_transactional_handling", record.input_id, quote)
    if claim.corrects_claim_id:
        return GateResult(Route.REJECT, "assertion_cannot_correct_claim", record.input_id, quote)
    if record.source_kind != "user_statement":
        return GateResult(Route.REVIEW, "external_source_not_user_confirmation", record.input_id, quote)
    if high_impact or record.confidentiality == "restricted":
        return GateResult(Route.REVIEW, "high_impact_or_restricted", record.input_id, quote)
    return GateResult(Route.AUTO_CANDIDATE, "eligible_for_db_conflict_check", record.input_id, quote)
