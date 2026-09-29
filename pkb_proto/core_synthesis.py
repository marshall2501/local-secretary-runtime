"""Deterministic cooperative synthesis for MAGI v0.

MELCHIOR is treated as a guard/scope signal and CASPER as an exploratory
proposal.  Neither member wins a vote.  Secretary Core combines them and keeps
the final authority.  v0 can narrow an external/compound proposal to a known
safe local prefix, but it never broadens scope beyond both proposals.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .core_capabilities import (
    CAPABILITY_REGISTRY,
    capability_allowed,
    safe_prefix,
    scope_rank,
)


@dataclass(frozen=True)
class SynthesisDecision:
    status: str
    next_step: str
    selected_capability: str | None = None
    reason: str | None = None
    scope_adjustment: str | None = None
    melchior_used: bool = True
    casper_used: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


def _melchior_next_step(melchior: dict) -> str:
    if melchior.get("status") == "ready" and melchior.get("selected_capability"):
        return "observe"
    return "clarify"


def _allowed_or_none(
    capability: str | None,
    permissions: dict[str, bool],
) -> str | None:
    if not capability:
        return None
    if capability not in CAPABILITY_REGISTRY:
        return None
    return capability if capability_allowed(capability, permissions) else None


def synthesize(
    melchior: dict,
    casper: dict,
    *,
    permissions: dict[str, bool] | None = None,
) -> SynthesisDecision:
    permissions = permissions or {
        "pkb_read": True,
        "finance_read": True,
        "web_research": True,
        "external_actions": False,
    }

    mel_step = _melchior_next_step(melchior)
    mel_cap = _allowed_or_none(melchior.get("selected_capability"), permissions)

    if casper.get("status") != "ok":
        if mel_step == "observe" and mel_cap:
            return SynthesisDecision(
                "ok",
                "observe",
                mel_cap,
                "CASPER is unavailable or invalid; continue with MELCHIOR's bounded read-only scope.",
                "casper_fallback",
                casper_used=False,
            )
        return SynthesisDecision(
            "ok",
            "clarify",
            None,
            "No valid CASPER proposal is available and MELCHIOR cannot safely scope a read.",
            "casper_fallback",
            casper_used=False,
        )

    cas_step = casper.get("next_step")
    cas_cap = _allowed_or_none(casper.get("proposed_action"), permissions)

    if cas_step == "respond":
        if mel_step == "clarify":
            return SynthesisDecision(
                "ok",
                "respond",
                None,
                "CASPER judges the shared observation sufficient; no broader capability is needed.",
                "observation_sufficient",
            )
        return SynthesisDecision(
            "ok",
            "observe",
            mel_cap,
            "MELCHIOR has a bounded read available; gather that evidence before responding.",
            "guarded_observation",
        )

    if cas_step == "clarify":
        if mel_step == "observe" and mel_cap:
            return SynthesisDecision(
                "ok",
                "observe",
                mel_cap,
                "A bounded MELCHIOR read can advance the goal before asking the user.",
                "clarification_deferred",
            )
        return SynthesisDecision(
            "ok",
            "clarify",
            None,
            "Both members provide no safe observation that can advance the goal without user input.",
            None,
        )

    if cas_step != "observe" or not cas_cap:
        if mel_step == "observe" and mel_cap:
            return SynthesisDecision(
                "ok",
                "observe",
                mel_cap,
                "CASPER's requested capability is unavailable; retain MELCHIOR's bounded scope.",
                "capability_guard",
            )
        return SynthesisDecision(
            "ok",
            "clarify",
            None,
            "No permitted observation capability can be selected safely.",
            "capability_guard",
        )

    if mel_step == "observe" and mel_cap:
        if mel_cap == cas_cap:
            return SynthesisDecision(
                "ok",
                "observe",
                mel_cap,
                "Both members support the same bounded observation.",
                None,
            )

        if safe_prefix(cas_cap) == mel_cap:
            return SynthesisDecision(
                "ok",
                "observe",
                mel_cap,
                "CASPER proposes a broader compound observation; use the MELCHIOR-safe prefix first.",
                "narrowed_to_safe_prefix",
            )

        if safe_prefix(mel_cap) == cas_cap:
            return SynthesisDecision(
                "ok",
                "observe",
                cas_cap,
                "CASPER selects the safe prefix of MELCHIOR's broader scope; start with the narrower observation.",
                "narrowed_to_safe_prefix",
            )

        chosen = mel_cap if scope_rank(mel_cap) <= scope_rank(cas_cap) else cas_cap
        return SynthesisDecision(
            "ok",
            "observe",
            chosen,
            "The members differ; start with the permitted lower-scope observation and re-evaluate afterward.",
            "least_scope",
        )

    prefix = safe_prefix(cas_cap)
    if prefix and capability_allowed(prefix, permissions):
        return SynthesisDecision(
            "ok",
            "observe",
            prefix,
            "MELCHIOR requests clarification, while CASPER can advance safely through the local prefix of a broader proposal.",
            "narrowed_to_safe_prefix",
        )

    if (CAPABILITY_REGISTRY[cas_cap]["risk"] == "local_read_only"
            and capability_allowed(cas_cap, permissions)):
        return SynthesisDecision(
            "ok",
            "observe",
            cas_cap,
            "MELCHIOR sees ambiguity, but CASPER identifies a permitted local read-only observation that can reduce it.",
            "safe_local_probe",
        )

    return SynthesisDecision(
        "ok",
        "clarify",
        None,
        "CASPER's proposal would broaden the ambiguous request to external scope without a safe local prefix.",
        "external_scope_blocked",
    )
