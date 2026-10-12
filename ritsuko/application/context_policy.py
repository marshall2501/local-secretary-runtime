"""Evaluate owner-defined MAGI context grants from one persisted Task snapshot.

No SQL, HTTP, Provider-specific security assumption or GUI logic belongs here.
An absent snapshot means a pre-migration/legacy Task and retains the old gate.
"""
from __future__ import annotations

from copy import deepcopy
from ipaddress import ip_address
from urllib.parse import urlsplit

CONTEXT_LABELS = ("llm.send_context.private", "llm.send_context.sensitive")
DECISIONS = {"allow", "deny", "require_confirmation", "redact"}
EXPOSURES = {"local", "lan", "public"}


def endpoint_scope(spec: dict) -> str:
    """Use the actual configured endpoint, not the provider's display name."""
    endpoint = str(spec.get("endpoint") or "").strip()
    if not endpoint:
        return "remote"  # Unknown is not verified loopback.
    try:
        host = (urlsplit(endpoint).hostname or "").lower().rstrip(".")
    except ValueError:
        return "remote"
    if host in {"localhost", "host.docker.internal", "secretary-ollama"}:
        return "local"
    try:
        if ip_address(host).is_loopback:
            return "local"
    except ValueError:
        pass
    return "remote"


def validate_policy_snapshot(snapshot: object) -> dict:
    """Validate persisted settings before using them to release an observation."""
    if not isinstance(snapshot, dict):
        raise ValueError("invalid_policy_snapshot")
    ident = str(snapshot.get("profile_id") or "").strip()
    if not ident:
        raise ValueError("policy_profile_required")
    revision = snapshot.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("invalid_policy_revision")
    grants = snapshot.get("grants")
    if not isinstance(grants, list):
        raise ValueError("policy_grants_required")
    pairs = set()
    safe = []
    for grant in grants:
        if not isinstance(grant, dict):
            raise ValueError("invalid_policy_grant")
        label = grant.get("label")
        scope = grant.get("destination_scope")
        decision = grant.get("decision")
        if label not in CONTEXT_LABELS or scope not in {"any", "local", "remote"}:
            # Other policy labels can be persisted but do not affect MAGI.
            continue
        if decision not in DECISIONS or (label, scope) in pairs:
            raise ValueError("invalid_policy_decision")
        pairs.add((label, scope))
        fields = grant.get("mask_rules") or {}
        if not isinstance(fields, dict):
            raise ValueError("invalid_mask_rules")
        mask_tokens = fields.get("tokens", [])
        if not isinstance(mask_tokens, list) or len(mask_tokens) > 20:
            raise ValueError("invalid_mask_tokens")
        if any(not isinstance(t, str) or not 1 <= len(t) <= 80 for t in mask_tokens):
            raise ValueError("invalid_mask_tokens")
        safe.append({"label": label, "destination_scope": scope,
                     "decision": decision, "mask_rules": {"tokens": mask_tokens}})
    return {"profile_id": ident, "revision": revision,
            "profile_name": str(snapshot.get("profile_name") or ""),
            "grants": safe}


def resolve_grant(
    grants: list[dict], label: str, scope: str,
) -> tuple[dict | None, str]:
    """Resolve exactly as RITSUKO does: target override, then any, else deny.

    Return the rule's provenance so the settings UI can distinguish a
    concrete grant from an inherited one. Missing is fail-closed, not an
    explicit deny stored in the database.
    """
    exact = next(
        (g for g in grants if g["label"] == label
         and g["destination_scope"] == scope), None,
    )
    if exact is not None:
        return exact, "exact"
    inherited = next(
        (g for g in grants if g["label"] == label
         and g["destination_scope"] == "any"), None,
    )
    if inherited is not None:
        return inherited, "inherited"
    return None, "missing"


def _grant_for(snapshot: dict, label: str, scope: str) -> dict | None:
    return resolve_grant(snapshot.get("grants") or [], label, scope)[0]


def _context_labels(session: dict) -> tuple[list[str], list[str]]:
    sources, labels = set(), set()
    for item in (session.get("observations") or []):
        if not isinstance(item, dict) or item.get("verified") is not True:
            continue
        level = item.get("confidentiality")
        if level in {"private", "sensitive"}:
            sources.add(str(item.get("source") or ""))
            labels.add("llm.send_context." + level)
    return sorted(labels), sorted(sources)


def restrict_members(session: dict, snapshot: dict) -> tuple[list[dict] | None, dict | None]:
    """Filter member specs; never convert deny/pending into allow."""
    policy = validate_policy_snapshot(snapshot)
    labels, sources = _context_labels(session)
    if not labels:
        return None, None
    allowed, withheld, redactions = [], [], {}
    for member in session.get("member_specs") or []:
        if not member.get("enabled"):
            continue
        scope = endpoint_scope(member)
        decisions = [_grant_for(policy, label, scope) for label in labels]
        denied = any(g is None or g["decision"] in {"deny", "require_confirmation"}
                     for g in decisions)
        if denied:
            withheld.append({"name": member.get("name"),
                             "provider": member.get("provider"),
                             "model": member.get("model")})
            continue
        allowed.append(deepcopy(member))
        redactions[member.get("name")] = sorted({
            token for grant in decisions
            if grant["decision"] == "redact"
            for token in grant["mask_rules"]["tokens"]
        })
    union_tokens = sorted({token for tokens in redactions.values() for token in tokens})
    info = {"mode": "configured_policy",
            "profile_id": policy["profile_id"],
            "profile_name": policy["profile_name"],
            "revision": policy["revision"],
            "private_sources": sources,
            "withheld_members": withheld,
            "redaction_member_counts": {key: len(values) for key, values in redactions.items()}}
    session["cloud_context_gate"] = {**deepcopy(info),
                                     "status": "applied" if allowed else "blocked_no_allowed_member"}
    if not allowed:
        session.update(status="stopped", next_step="cloud_context_gate_no_allowed_member")
        return [], None
    return allowed, {**info, "redaction_tokens": union_tokens}


def redact_observation_text(text: str, tokens: list[str]) -> str:
    """Literal targeted substitutions, not heuristic global PII rewriting."""
    for token in sorted(set(tokens), key=len, reverse=True):
        text = text.replace(token, "[MASKED]")
    return text
