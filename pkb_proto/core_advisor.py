"""Shadow-mode LLM advisor for Secretary Core Orient.

The advisor never selects or executes the real capability. It only proposes one
candidate from the supplied registry so model quality can be measured safely.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .core_capabilities import CAPABILITY_REGISTRY


OLLAMA = "http://127.0.0.1:11434"
PREFERRED_MODELS = ("llama3.1:8b", "qwen3.5:9b")
ADVISOR_FIELDS = ("situation", "missing_information", "next_step", "proposed_action", "reason", "expected_result")


@dataclass(frozen=True)
class AdvisorResult:
    status: str
    comparison: str
    model: str | None = None
    situation: str | None = None
    missing_information: tuple[str, ...] = ()
    next_step: str | None = None
    proposed_action: str | None = None
    reason: str | None = None
    expected_result: str | None = None
    error: str | None = None
    timeout_seconds: float | None = None
    request_context: dict | None = None
    response_diagnostic: dict | None = None

    def as_dict(self) -> dict:
        value = asdict(self)
        value["missing_information"] = list(self.missing_information)
        return value


def list_chat_models(timeout: float = 3.0) -> list[str]:
    req = Request(OLLAMA + "/api/tags", method="GET")
    with urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise ValueError("Unexpected Ollama model-list response")
    result = []
    for item in models:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name and not name.split(":", 1)[0].endswith("-embed-text"):
            result.append(name)
    return result


def choose_model(models: list[str], requested: str | None = None) -> str:
    configured = (requested or "").strip() or os.environ.get("LSA_CORE_ADVISOR_MODEL", "").strip()
    if configured:
        if configured not in models:
            raise ValueError("Configured Core advisor model is not installed")
        return configured
    for preferred in PREFERRED_MODELS:
        if preferred in models:
            return preferred
    if not models:
        raise ValueError("No local chat model is installed")
    return models[0]


def _messages(context: dict) -> list[dict[str, str]]:
    system = (
        "You are CASPER, the Orient advisor for a local personal secretary system. "
        "You only propose; you never execute tools and never change Task state. "
        "Return one JSON object with exactly these keys: situation, missing_information, "
        "next_step, proposed_action, reason, expected_result. next_step must be one of "
        "observe, respond, clarify. Use observe when another read-only capability should gather "
        "evidence; then proposed_action must be exactly one name from available_capabilities. "
        "Use respond when the observation_pack already contains enough evidence to answer; "
        "then proposed_action must be null. Use clarify only when progress is blocked by "
        "information that must come from the user; then proposed_action must be null and "
        "missing_information must contain the blocking items. For observe or respond, "
        "missing_information must be empty. Do not list optional preferences such as output "
        "format or category unless they truly block progress. Do not invent capabilities. "
        "The observation_pack contains facts already observed from local read-only systems. "
        "Treat those facts as available evidence; do not ask how to access information that "
        "is already present there. Prefer the minimum next step that can advance the stated goal. "
        "If current local state and current public information must be compared and more evidence "
        "is needed, observe with pkb_web_compare rather than separate unsupported free-form steps."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]


def diagnose_response(raw: str) -> dict:
    """Return a user-visible, reasoning-safe diagnostic of model output.

    Only the contracted Advisor fields are retained. Unknown key names are
    recorded without their values. Non-JSON prose is never persisted.
    """
    diagnostic = {
        "json_valid": False,
        "raw_length": len(raw),
        "contract_fields": list(ADVISOR_FIELDS),
        "safe_response": None,
        "field_types": {},
        "missing_keys": [],
        "unexpected_keys": [],
    }
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return diagnostic
    diagnostic["json_valid"] = True
    if not isinstance(parsed, dict):
        diagnostic["response_type"] = type(parsed).__name__
        return diagnostic

    safe = {key: parsed.get(key) for key in ADVISOR_FIELDS if key in parsed}
    diagnostic["safe_response"] = safe
    diagnostic["field_types"] = {
        key: type(parsed[key]).__name__
        for key in ADVISOR_FIELDS
        if key in parsed
    }
    diagnostic["missing_keys"] = [
        key for key in ADVISOR_FIELDS if key not in parsed
    ]
    diagnostic["unexpected_keys"] = sorted(
        str(key) for key in parsed.keys() if key not in ADVISOR_FIELDS
    )
    return diagnostic


def inspect_output(
    raw: object,
    available: set[str],
    current_selection: str | None,
    melchior_next_step: str,
) -> AdvisorResult:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return AdvisorResult("invalid", "invalid", error="invalid_json")
    expected = set(ADVISOR_FIELDS)
    if not isinstance(raw, dict) or set(raw) != expected:
        return AdvisorResult("invalid", "invalid", error="invalid_response_schema")

    situation = raw["situation"]
    missing = raw["missing_information"]
    next_step = raw["next_step"]
    proposed = raw["proposed_action"]
    reason = raw["reason"]
    expected_result = raw["expected_result"]

    if not isinstance(situation, str) or not situation.strip():
        return AdvisorResult("invalid", "invalid", error="invalid_situation")
    if not isinstance(missing, list) or not all(isinstance(x, str) for x in missing):
        return AdvisorResult("invalid", "invalid", error="invalid_missing_information")
    if next_step not in {"observe", "respond", "clarify"}:
        return AdvisorResult("invalid", "invalid", error="invalid_next_step")
    if proposed is not None and (not isinstance(proposed, str) or proposed not in available):
        return AdvisorResult("invalid", "invalid", error="unknown_capability")
    if next_step == "observe" and proposed is None:
        return AdvisorResult("invalid", "invalid", error="observe_requires_capability")
    if next_step in {"respond", "clarify"} and proposed is not None:
        return AdvisorResult("invalid", "invalid", error="non_observe_capability_not_allowed")
    normalized_missing = tuple(x.strip() for x in missing if x.strip())
    if next_step == "clarify" and not normalized_missing:
        return AdvisorResult("invalid", "invalid", error="clarify_requires_missing_information")
    if next_step in {"observe", "respond"} and normalized_missing:
        return AdvisorResult("invalid", "invalid", error="non_clarify_missing_information")
    if not isinstance(reason, str) or not reason.strip():
        return AdvisorResult("invalid", "invalid", error="invalid_reason")
    if not isinstance(expected_result, str) or not expected_result.strip():
        return AdvisorResult("invalid", "invalid", error="invalid_expected_result")

    comparison = "mismatch"
    if next_step == melchior_next_step:
        if next_step == "observe":
            comparison = "match" if proposed == current_selection else "mismatch"
        else:
            comparison = "match"

    return AdvisorResult(
        "ok",
        comparison,
        situation=situation.strip(),
        missing_information=normalized_missing,
        next_step=next_step,
        proposed_action=proposed,
        reason=reason.strip(),
        expected_result=expected_result.strip(),
    )


def advise(
    request_text: str,
    *,
    current_selection: str | None,
    melchior_next_step: str,
    task_state: str = "received",
    observations: object | None = None,
    permissions: dict[str, bool] | None = None,
    timeout: float = 60.0,
    model: str | None = None,
) -> AdvisorResult:
    if os.environ.get("LSA_CORE_ADVISOR_ENABLED", "1").strip().lower() in {"0", "false", "off", "no"}:
        return AdvisorResult(
            "disabled", "unavailable", model=model,
            error="advisor_disabled", timeout_seconds=timeout,
        )

    available = set(CAPABILITY_REGISTRY)
    context = {
        "goal": request_text,
        "task_state": task_state,
        "magi_member": "CASPER",
        "observation_pack": observations if observations is not None else {},
        "available_capabilities": [
            {"name": name, **CAPABILITY_REGISTRY[name]}
            for name in sorted(available)
        ],
        "permissions": permissions or {
            "pkb_read": True,
            "finance_read": True,
            "web_research": True,
            "external_actions": False,
        },
    }

    attempted_model = (model or "").strip() or None
    try:
        selected_model = choose_model(list_chat_models(), model)
        attempted_model = selected_model
        payload = {
            "model": selected_model,
            "stream": False,
            "format": "json",
            "messages": _messages(context),
            "options": {"temperature": 0, "num_predict": 500},
        }
        req = Request(
            OLLAMA + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            outer = json.load(resp)
        message = outer.get("message") if isinstance(outer, dict) else None
        raw = message.get("content") if isinstance(message, dict) else None
        if not isinstance(raw, str):
            return AdvisorResult(
                "invalid", "invalid", model=selected_model,
                error="missing_model_content", timeout_seconds=timeout,
                request_context=context,
            )
        response_diagnostic = diagnose_response(raw)
        checked = inspect_output(
            raw,
            available,
            current_selection,
            melchior_next_step,
        )
        return AdvisorResult(
            checked.status,
            checked.comparison,
            model=selected_model,
            situation=checked.situation,
            missing_information=checked.missing_information,
            next_step=checked.next_step,
            proposed_action=checked.proposed_action,
            reason=checked.reason,
            expected_result=checked.expected_result,
            error=checked.error,
            timeout_seconds=timeout,
            request_context=context,
            response_diagnostic=response_diagnostic,
        )
    except (OSError, HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return AdvisorResult(
            "unavailable",
            "unavailable",
            model=attempted_model,
            error=type(exc).__name__,
            timeout_seconds=timeout,
            request_context=context,
        )
