"""Bounded cooperative execution policy for MAGI v0.

This module contains deterministic Secretary Core guards.  It intentionally
does not call models, databases or tools so the policy can be tested directly.
"""
from __future__ import annotations

from copy import deepcopy


MAX_COOPERATIVE_CYCLES = 2
AMBIGUOUS_AUTO_READ_CAPABILITIES = frozenset({"pkb_search"})


def can_auto_execute_ambiguous_probe(
    melchior: dict,
    synthesis: dict,
    *,
    executed_capabilities: tuple[str, ...] = (),
) -> bool:
    """Allow only a first, local PKB probe for an otherwise ambiguous request."""
    if melchior.get("status") == "ready":
        return False
    if synthesis.get("status") != "ok" or synthesis.get("next_step") != "observe":
        return False
    capability = synthesis.get("selected_capability")
    if capability not in AMBIGUOUS_AUTO_READ_CAPABILITIES:
        return False
    return capability not in executed_capabilities


def extend_observation_pack(
    observation_pack: dict,
    execution: dict,
    *,
    cycle: int,
) -> dict:
    """Return a bounded v2 observation containing the Task's new read result."""
    updated = deepcopy(observation_pack)
    updated["version"] = "magi_observation_v2"
    prior = list(updated.get("task_observations") or [])
    result = execution.get("result") or {}
    items = list(result.get("items") or [])
    observation = {
        "cycle": cycle,
        "capability": execution.get("capability"),
        "result_count": int(execution.get("total") or 0),
        "result_kind": result.get("result_kind"),
        "answer": execution.get("answer"),
        "evidence_preview": items[:8],
    }
    prior.append(observation)
    updated["task_observations"] = prior[-MAX_COOPERATIVE_CYCLES:]
    updated["task_progress"] = {
        "executed_capabilities": [
            item.get("capability")
            for item in updated["task_observations"]
            if item.get("capability")
        ],
        "cycle": cycle + 1,
        "max_cycles": MAX_COOPERATIVE_CYCLES,
    }
    return updated


def decide_after_observation(
    synthesis: dict,
    *,
    execution: dict,
    executed_capabilities: tuple[str, ...],
) -> dict:
    """Stop after the second Orient instead of silently broadening scope."""
    total = int(execution.get("total") or 0)
    answer = str(execution.get("answer") or "").strip()

    if synthesis.get("next_step") == "respond":
        return {
            "next_step": "respond",
            "message": answer,
            "question": None,
            "reason": "second_cycle_respond",
        }

    if synthesis.get("next_step") == "clarify":
        return {
            "next_step": "clarify",
            "message": answer if total > 0 else "PKBの安全な範囲を確認しましたが、十分な記録がありません。",
            "question": (
                "PKBで確認できる範囲は確認しました。"
                " さらに何を確認したいか指定してください。"
                " 例: GPUドライバー、NIC、構成、更新履歴。"
            ),
            "reason": "second_cycle_clarify",
        }

    capability = synthesis.get("selected_capability")
    if synthesis.get("next_step") == "observe" and capability in executed_capabilities:
        if total > 0:
            return {
                "next_step": "respond",
                "message": answer,
                "question": None,
                "reason": "repeat_probe_stopped_with_evidence",
            }
        return {
            "next_step": "clarify",
            "message": "同じ安全なPKB検索を繰り返しても新しい情報は得られませんでした。",
            "question": (
                "PKBだけでは対象を十分に確認できません。"
                " 確認したい項目を指定してください。"
            ),
            "reason": "repeat_probe_stopped_without_evidence",
        }

    # v0 never broadens an ambiguous request to a second different capability.
    if total > 0:
        return {
            "next_step": "respond",
            "message": answer,
            "question": None,
            "reason": "cycle_limit_return_local_evidence",
        }
    return {
        "next_step": "clarify",
        "message": "安全なPKB確認だけでは十分な情報を得られませんでした。",
        "question": "確認したい項目をもう少し具体的に指定してください。",
        "reason": "cycle_limit_clarify",
    }
