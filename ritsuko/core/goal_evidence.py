# Provenance-Origin: original
# Provenance-AI: AI-generated
"""RITSUKO's deterministic evidence gate for a MAGI Goal response.

The LLM judges semantic fulfillment, but cannot mint Result IDs or claim an
unexecuted read was verified. The same check is used before applying the
judgment and when projecting the persisted Task's terminal state.
"""
from __future__ import annotations

from ritsuko.application.observation_sources import AUTO_READ_SOURCES


def check_goal_response_evidence(
    session: dict, evidence: dict,
) -> tuple[str, dict[str, dict]]:
    """Check evidence against this Task's recorded observations.

    Args:
        session: Persisted MAGI session containing observations and read state.
        evidence: Validated Goal decision's evidence object.

    Returns:
        A machine-checkable status and this Task's verified referenced results.
        Source authority and semantic relevance remain MAGI responsibilities.
    """
    references = list(evidence.get("result_ids") or [])
    recorded = {
        str(observation["result_id"]): observation
        for observation in session.get("observations") or []
        if isinstance(observation, dict)
        and observation.get("source") in AUTO_READ_SOURCES
        and observation.get("result_id")
    }
    # Referencing an absent or unverified result must never upgrade it to proof.
    if any(
        result_id not in recorded or recorded[result_id].get("verified") is not True
        for result_id in references
    ):
        return "unrecorded_task_result_reference", {}

    if evidence.get("basis") == "result_refs":
        if not references:
            return "goal_evidence_missing", {}
        return "verified_results", {
            result_id: recorded[result_id] for result_id in references
        }

    if evidence.get("basis") == "not_required":
        if references:
            return "goal_evidence_missing", {}
        if (
            session.get("requires_source", session.get("requires_web", False))
            or session.get("requires_web")
            or session.get("tool_read_executed")
        ):
            return "required_source_not_observed", {}
        return "source_free", {}

    return "goal_evidence_missing", {}
