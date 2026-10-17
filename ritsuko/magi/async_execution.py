"""Async RITSUKO <-> MAGI execution path.

This module owns the active asynchronous MAGI Provider transport. MAGI Task
Session lifecycle is owned by session_workflow.py, not by Provider transport.

Properties:
- provider calls are cancellable async HTTP;
- enabled MAGI members run concurrently;
- each member owns an independent hard deadline;
- one member timeout/failure does not block other valid members;
- parent cancellation propagates through the whole panel;
- RITSUKO still owns turn limits, stop/resume and final control.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import time
from urllib.parse import quote

import anyio

from integrations.connections.credential_resolver import (
    CredentialResolutionError,
    env_name_to_credential_ref,
    resolve_connection_credential,
)
from .transport_contract import (
    AsyncHTTPStatusError,
    AsyncRequestTimeout,
    AsyncRetryExhausted,
    AsyncTransportError,
)


async def _transport_not_configured(*_args, **_kwargs):
    raise RuntimeError("async_transport_not_configured")


# Composition roots replace this callable with the concrete async transport.
# Keeping this module-level name preserves the existing test patch surface.
request_json_with_retry = _transport_not_configured
from .client import OLLAMA
from .goal_contract import GOAL_SCHEMA, GOAL_SYSTEM, validate_goal_turn
from .dialogue import (
    CLASSIFY_QUESTION,
    MAX_TURNS,
    PROMPT_VERSION,
    SYSTEM,
    _CLASSIFICATION_SCHEMA,
    _DETAIL_SCHEMA,
    _compose_question,
    _extract_gemini_text,
    _gemini_response_schema,
    _extract_openai_output_text,
    _normalized_member_specs,
    _request_signatures,
    _latest_verified_pkb_observation,
    _grounded_empty_finance_answer,
    _wait_for_user_after_exhausted_pkb,
    _turn_limit,
    _extend_turn_limit_for_user_resume,
    _select_question_purpose,
    _stop_between_turns,
    select_weighted_consensus,
    validate_turn,
)
from ritsuko.application.context_policy import (
    restrict_members as restrict_members_by_profile,
    redact_observation_text,
)
from .settings import (
    DEFAULT_RETRY_HTTP_CODES,
    DEFAULT_RETRY_WITHIN_TURN,
    DEFAULT_TIMEOUT_SECONDS,
)
from integrations.llm.ollama_runtime import configured_magi_num_predict, normalize_context_tokens, normalize_magi_num_predict
from .protocol import default_resource_catalog
from .runtime_context import create_runtime_context, runtime_context_from_session
from .judgment_strategies import strategy_for_name, strategy_for_session

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _provider_failure(provider: str, exc: Exception) -> dict:
    retry_diagnostic = {}
    cause = exc
    if isinstance(exc, AsyncRetryExhausted):
        retry_diagnostic = dict(exc.diagnostic)
        cause = exc.cause

    if isinstance(cause, AsyncRequestTimeout):
        error = "timeout"
        diagnostic = {"provider": provider, "error": error}
    elif isinstance(cause, AsyncHTTPStatusError):
        error = "HTTPError"
        diagnostic = {
            "provider": provider,
            "error": error,
            "http_status": cause.status_code,
            "provider_status": cause.provider_status,
            "provider_message": cause.provider_message,
        }
    else:
        error = type(cause).__name__
        diagnostic = {"provider": provider, "error": error}
    diagnostic.update(retry_diagnostic)
    return {
        "status": "unavailable",
        "response": None,
        "errors": [error],
        "diagnostic": diagnostic,
    }


async def _call_ollama_guided_async(
    envelope: dict,
    *,
    model: str,
    timeout: float,
    base_url: str | None = None,
    context_window_tokens: int | None = None,
    ollama_num_predict: int | None = None,
    retry_within_turn: bool = DEFAULT_RETRY_WITHIN_TURN,
    retry_http_codes: tuple[int, ...] | list[int] = DEFAULT_RETRY_HTTP_CODES,
) -> dict:
    stage = envelope["stage"]
    schema = (GOAL_SCHEMA if stage == "goal" else
              _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA)
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "messages": [
            {"role": "system", "content": GOAL_SYSTEM if stage == "goal" else SYSTEM},
            {"role": "user", "content": json.dumps(envelope, ensure_ascii=False)},
        ],
        "options": {
            "temperature": 0,
            "num_predict": normalize_magi_num_predict(
                ollama_num_predict
                if ollama_num_predict is not None
                else configured_magi_num_predict()
            ),
            "num_ctx": normalize_context_tokens(context_window_tokens),
        },
    }
    endpoint = str(base_url or OLLAMA).strip().rstrip("/")
    try:
        outer, retry_diagnostic = await request_json_with_retry(
            "POST",
            endpoint + "/api/chat",
            headers={"Content-Type": "application/json"},
            json_body=payload,
            timeout=timeout,
            retry_enabled=retry_within_turn,
            retry_http_codes=retry_http_codes,
        )
        msg = outer.get("message") if isinstance(outer, dict) else None
        raw = msg.get("content") if isinstance(msg, dict) else None
        thinking = msg.get("thinking") if isinstance(msg, dict) else None
        diagnostic = {
            "provider": "ollama",
            "content_length": len(raw) if isinstance(raw, str) else 0,
            "thinking_length": len(thinking) if isinstance(thinking, str) else 0,
            "done_reason": outer.get("done_reason") if isinstance(outer, dict) else None,
            "eval_count": outer.get("eval_count") if isinstance(outer, dict) else None,
            "num_predict": payload["options"]["num_predict"],
            **retry_diagnostic,
        }
        if not isinstance(raw, str):
            return {
                "status": "invalid",
                "response": None,
                "errors": ["missing_content"],
                "diagnostic": diagnostic,
            }
        try:
            data = json.loads(raw)
        except ValueError:
            return {
                "status": "invalid",
                "response": None,
                "errors": ["invalid_json"],
                "diagnostic": diagnostic,
            }
        errors = validate_goal_turn(data) if stage == "goal" else validate_turn(stage, data)
        return {
            "status": "ok" if not errors else "invalid",
            "response": data,
            "errors": errors,
            "diagnostic": diagnostic,
        }
    except (
        AsyncRetryExhausted,
        OSError,
        ValueError,
    ) as exc:
        return _provider_failure("ollama", exc)


async def _call_openai_guided_async(
    envelope: dict,
    *,
    model: str,
    timeout: float,
    base_url: str | None = None,
    connection_id: str | None = None,
    credential_ref: str | None = None,
    credential_env: str | None = None,
    retry_within_turn: bool = DEFAULT_RETRY_WITHIN_TURN,
    retry_http_codes: tuple[int, ...] | list[int] = DEFAULT_RETRY_HTTP_CODES,
) -> dict:
    resolved_ref = credential_ref or env_name_to_credential_ref(
        credential_env or "OPENAI_API_KEY"
    )
    try:
        api_key = resolve_connection_credential(connection_id, resolved_ref)
    except CredentialResolutionError:
        return {
            "status": "unavailable",
            "response": None,
            "errors": ["missing_provider_credential"],
            "diagnostic": {
                "provider": "openai",
                "error": "missing_provider_credential",
                "credential_ref": resolved_ref,
            },
        }
    stage = envelope["stage"]
    schema = (GOAL_SCHEMA if stage == "goal" else
              _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA)
    endpoint = str(
        base_url or os.environ.get("OPENAI_BASE_URL") or OPENAI_DEFAULT_BASE_URL
    ).strip().rstrip("/")
    payload = {
        "model": model,
        "store": False,
        "instructions": GOAL_SYSTEM if stage == "goal" else SYSTEM,
        "input": json.dumps(envelope, ensure_ascii=False),
        "max_output_tokens": 2100 if stage == "goal" else 1600,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "magi_" + stage,
                "strict": True,
                "schema": schema,
            }
        },
    }
    try:
        outer, retry_diagnostic = await request_json_with_retry(
            "POST",
            endpoint + "/responses",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
            },
            json_body=payload,
            timeout=timeout,
            retry_enabled=retry_within_turn,
            retry_http_codes=retry_http_codes,
        )
        raw = _extract_openai_output_text(outer)
        usage = outer.get("usage") if isinstance(outer, dict) else None
        diagnostic = {
            "provider": "openai",
            "response_status": outer.get("status") if isinstance(outer, dict) else None,
            "response_id": outer.get("id") if isinstance(outer, dict) else None,
            "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
            "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
            **retry_diagnostic,
        }
        if not isinstance(raw, str):
            return {
                "status": "invalid",
                "response": None,
                "errors": ["missing_content"],
                "diagnostic": diagnostic,
            }
        try:
            data = json.loads(raw)
        except ValueError:
            return {
                "status": "invalid",
                "response": None,
                "errors": ["invalid_json"],
                "diagnostic": diagnostic,
            }
        errors = validate_goal_turn(data) if stage == "goal" else validate_turn(stage, data)
        return {
            "status": "ok" if not errors else "invalid",
            "response": data,
            "errors": errors,
            "diagnostic": diagnostic,
        }
    except (
        AsyncRetryExhausted,
        OSError,
        ValueError,
    ) as exc:
        return _provider_failure("openai", exc)


async def _call_gemini_guided_async(
    envelope: dict,
    *,
    model: str,
    timeout: float,
    base_url: str | None = None,
    connection_id: str | None = None,
    credential_ref: str | None = None,
    credential_env: str | None = None,
    retry_within_turn: bool = DEFAULT_RETRY_WITHIN_TURN,
    retry_http_codes: tuple[int, ...] | list[int] = DEFAULT_RETRY_HTTP_CODES,
) -> dict:
    resolved_ref = credential_ref or env_name_to_credential_ref(
        credential_env or "GEMINI_API_KEY"
    )
    try:
        api_key = resolve_connection_credential(connection_id, resolved_ref)
    except CredentialResolutionError:
        return {
            "status": "unavailable",
            "response": None,
            "errors": ["missing_provider_credential"],
            "diagnostic": {
                "provider": "gemini",
                "error": "missing_provider_credential",
                "credential_ref": resolved_ref,
            },
        }
    stage = envelope["stage"]
    schema = (GOAL_SCHEMA if stage == "goal" else
              _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA)
    endpoint = str(
        base_url or os.environ.get("GEMINI_BASE_URL") or GEMINI_DEFAULT_BASE_URL
    ).strip().rstrip("/")
    payload = {
        "systemInstruction": {"parts": [{"text": GOAL_SYSTEM if stage == "goal" else SYSTEM}]},
        "contents": [{
            "role": "user",
            "parts": [{"text": json.dumps(envelope, ensure_ascii=False)}],
        }],
        "generationConfig": {
            "maxOutputTokens": 2100 if stage == "goal" else 1600,
            "responseFormat": {
                "text": {
                    "mimeType": "APPLICATION_JSON",
                    "schema": _gemini_response_schema(schema),
                }
            },
        },
    }
    try:
        outer, retry_diagnostic = await request_json_with_retry(
            "POST",
            endpoint + "/models/" + quote(model, safe="") + ":generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json_body=payload,
            timeout=timeout,
            retry_enabled=retry_within_turn,
            retry_http_codes=retry_http_codes,
        )
        raw = _extract_gemini_text(outer)
        usage = outer.get("usageMetadata") if isinstance(outer, dict) else None
        diagnostic = {
            "provider": "gemini",
            "prompt_tokens": usage.get("promptTokenCount") if isinstance(usage, dict) else None,
            "candidate_tokens": usage.get("candidatesTokenCount") if isinstance(usage, dict) else None,
            "total_tokens": usage.get("totalTokenCount") if isinstance(usage, dict) else None,
            **retry_diagnostic,
        }
        if not isinstance(raw, str):
            return {
                "status": "invalid",
                "response": None,
                "errors": ["missing_content"],
                "diagnostic": diagnostic,
            }
        try:
            data = json.loads(raw)
        except ValueError:
            return {
                "status": "invalid",
                "response": None,
                "errors": ["invalid_json"],
                "diagnostic": diagnostic,
            }
        errors = validate_goal_turn(data) if stage == "goal" else validate_turn(stage, data)
        return {
            "status": "ok" if not errors else "invalid",
            "response": data,
            "errors": errors,
            "diagnostic": diagnostic,
        }
    except (
        AsyncRetryExhausted,
        OSError,
        ValueError,
    ) as exc:
        return _provider_failure("gemini", exc)


async def _call_panel_member_async(
    spec: dict,
    envelope: dict,
    *,
    timeout: float,
) -> dict:
    member_envelope = deepcopy(envelope)
    member_envelope["magi_member"] = spec["name"]
    member_timeout = float(spec.get("timeout_seconds") or timeout)
    provider = spec["provider"]
    started = time.perf_counter()

    if provider == "ollama":
        result = await _call_ollama_guided_async(
            member_envelope,
            model=spec["model"],
            timeout=member_timeout,
            base_url=spec.get("endpoint"),
            context_window_tokens=spec.get("context_window_tokens"),
            ollama_num_predict=spec.get("ollama_num_predict"),
            retry_within_turn=bool(spec.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)),
            retry_http_codes=spec.get("retry_http_codes") or DEFAULT_RETRY_HTTP_CODES,
        )
    elif provider == "openai":
        result = await _call_openai_guided_async(
            member_envelope,
            model=spec["model"],
            timeout=member_timeout,
            base_url=spec.get("endpoint"),
            connection_id=spec.get("connection_id"),
            credential_ref=spec.get("credential_ref"),
            credential_env=spec.get("credential_env"),
            retry_within_turn=bool(spec.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)),
            retry_http_codes=spec.get("retry_http_codes") or DEFAULT_RETRY_HTTP_CODES,
        )
    elif provider == "gemini":
        result = await _call_gemini_guided_async(
            member_envelope,
            model=spec["model"],
            timeout=member_timeout,
            base_url=spec.get("endpoint"),
            connection_id=spec.get("connection_id"),
            credential_ref=spec.get("credential_ref"),
            credential_env=spec.get("credential_env"),
            retry_within_turn=bool(spec.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)),
            retry_http_codes=spec.get("retry_http_codes") or DEFAULT_RETRY_HTTP_CODES,
        )
    else:
        result = {
            "status": "unavailable",
            "response": None,
            "errors": ["unsupported_provider"],
            "diagnostic": {"provider": provider, "error": "unsupported_provider"},
        }

    return {
        "name": spec["name"],
        "profile_id": spec.get("profile_id"),
        "connection_id": spec.get("connection_id"),
        "provider": provider,
        "model": spec["model"],
        "weight": spec["weight"],
        "timeout_seconds": spec.get("timeout_seconds"),
        "context_window_tokens": spec.get("context_window_tokens"),
        "ollama_num_predict": spec.get("ollama_num_predict"),
        "retry_http_codes": list(spec.get("retry_http_codes") or []),
        "retry_within_turn": bool(spec.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "status": result.get("status"),
        "response": deepcopy(result.get("response")),
        "errors": list(result.get("errors") or []),
        "diagnostic": deepcopy(result.get("diagnostic") or {}),
    }


def _emit_member_progress(callback, event: dict) -> None:
    if callback is None:
        return
    try:
        callback(deepcopy(event))
    except Exception:
        # Progress is presentation-only and must never fail MAGI execution.
        return


async def call_guided_panel_async(
    envelope: dict,
    *,
    model: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    member_specs: list[dict] | None = None,
    on_member_progress=None,
) -> dict:
    """Run enabled provider-independent MAGI members concurrently and cancellably."""
    specs = [
        spec for spec in _normalized_member_specs(member_specs, model)
        if spec["enabled"]
    ]
    member_results: list[dict | None] = [None] * len(specs)
    task_id = str(envelope.get("task_id") or "")
    turn = int(envelope.get("turn") or 0)

    for spec in specs:
        _emit_member_progress(on_member_progress, {
            "task_id": task_id,
            "turn": turn,
            "member": spec["name"],
            "provider": spec["provider"],
            "model": spec["model"],
            "state": "queued",
        })

    async def run_one(index: int, spec: dict) -> None:
        _emit_member_progress(on_member_progress, {
            "task_id": task_id,
            "turn": turn,
            "member": spec["name"],
            "provider": spec["provider"],
            "model": spec["model"],
            "state": "running",
        })
        try:
            member_results[index] = await _call_panel_member_async(
                spec, envelope, timeout=timeout
            )
        except Exception as exc:
            member_results[index] = {
                "name": spec["name"],
                "profile_id": spec.get("profile_id"),
                "connection_id": spec.get("connection_id"),
                "provider": spec["provider"],
                "model": spec["model"],
                "weight": spec["weight"],
                "timeout_seconds": spec.get("timeout_seconds"),
                "context_window_tokens": spec.get("context_window_tokens"),
                "ollama_num_predict": spec.get("ollama_num_predict"),
                "retry_http_codes": list(spec.get("retry_http_codes") or []),
                "retry_within_turn": bool(spec.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)),
                "status": "unavailable",
                "response": None,
                "errors": [type(exc).__name__],
                "diagnostic": {"error": type(exc).__name__},
            }

        item = member_results[index] or {}
        result_state = str(item.get("status") or "unavailable")
        _emit_member_progress(on_member_progress, {
            "task_id": task_id,
            "turn": turn,
            "member": spec["name"],
            "provider": spec["provider"],
            "model": spec["model"],
            "state": "completed" if result_state == "ok" else result_state,
            "elapsed_seconds": item.get("elapsed_seconds"),
            "validated_summary": (
                (item.get("response") or {}).get("reason")
                or (item.get("response") or {}).get("understood_request")
                or (item.get("response") or {}).get("answer_candidate")
                or ""
            ) if isinstance(item.get("response"), dict) else "",
            "error_kind": (
                str((item.get("errors") or [""])[0])
                if item.get("errors") else ""
            ),
        })

    async with anyio.create_task_group() as tg:
        for index, spec in enumerate(specs):
            tg.start_soon(run_one, index, spec)

    completed = [item for item in member_results if isinstance(item, dict)]
    consensus = select_weighted_consensus(envelope["stage"], completed)
    status = consensus["status"]
    return {
        "status": status,
        "response": deepcopy(consensus.get("response")),
        "errors": [] if status == "ok" else [str(consensus.get("reason") or status)],
        "diagnostic": {
            "mode": "weighted_panel_async",
            "enabled_members": [spec["name"] for spec in specs],
            "assignments": [
                {
                    "name": spec["name"],
                    "profile_id": spec.get("profile_id"),
                    "connection_id": spec.get("connection_id"),
                    "provider": spec["provider"],
                    "model": spec["model"],
                    "weight": spec["weight"],
                    "timeout_seconds": spec["timeout_seconds"],
                    "context_window_tokens": spec.get("context_window_tokens"),
                    "ollama_num_predict": spec.get("ollama_num_predict"),
                    "retry_http_codes": list(spec.get("retry_http_codes") or []),
                    "retry_within_turn": bool(spec.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)),
                }
                for spec in specs
            ],
            "valid_members": list(consensus.get("valid_members") or []),
        },
        "member_results": completed,
        "consensus": consensus,
    }


def apply_context_policy_to_envelope(envelope: dict, context_policy: dict | None) -> dict:
    """Apply one Task's allowed masking without exposing masking tokens.

    The same transformation is used for both ordinary and LLM flow judgments.
    """
    if not context_policy:
        return envelope
    tokens = list(context_policy.get("redaction_tokens") or [])
    safe_policy = {
        key: deepcopy(value) for key, value in context_policy.items()
        if key != "redaction_tokens"
    }
    if tokens:
        def redact_tree(value):
            if isinstance(value, str):
                return redact_observation_text(value, tokens)
            if isinstance(value, list):
                return [redact_tree(item) for item in value]
            if isinstance(value, dict):
                return {key: redact_tree(item) for key, item in value.items()}
            return value
        envelope = redact_tree(envelope)
    envelope["context_policy"] = deepcopy(safe_policy)
    return envelope


async def _send_async(
    session: dict,
    stage: str,
    question_purpose: str,
    prompt: str,
    *,
    timeout: float,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
    on_member_progress=None,
    member_specs_override: list[dict] | None = None,
    context_policy: dict | None = None,
) -> dict | None:
    if len(session["turns"]) >= _turn_limit(session):
        session.update(status="stopped", next_step="max_turns_reached")
        return None
    if _stop_between_turns(session, stop_requested):
        return None

    turn_number = len(session["turns"]) + 1
    turn_started_at = datetime.now(timezone.utc).isoformat()
    envelope = {
        "protocol_variant": "state_driven_question_experiment",
        "prompt_version": session["prompt_version"],
        "task_id": session["task_id"],
        "turn": turn_number,
        "magi_member": "MAGI_PANEL",
        "stage": stage,
        "question_purpose": question_purpose,
        "question_from_ritsuko": prompt,
        "user_input": {"raw": session["user_raw"]},
    }
    runtime_context = runtime_context_from_session(session)
    if runtime_context is not None:
        envelope["runtime_context"] = runtime_context
    if stage == "classify" and session.get("conversation_context"):
        envelope["conversation_context"] = deepcopy(session["conversation_context"][-4:])
    if stage != "classify":
        envelope.update({
            "resource_catalog": default_resource_catalog(),
            "task_context": {
                "classification": deepcopy(session.get("classification")),
                "previous_detail": deepcopy(session.get("detail")),
                "pending_information_requests": deepcopy(session.get("pending_requests") or []),
            },
            "observations": deepcopy(session["observations"]),
        })
    envelope = apply_context_policy_to_envelope(envelope, context_policy)
    if on_turn_start is not None:
        on_turn_start(turn_number)

    active_specs = (
        member_specs_override
        if member_specs_override is not None
        else session.get("member_specs")
    )
    call_options = {
        "model": session.get("model") or "",
        "timeout": timeout,
        "member_specs": active_specs,
    }
    if on_member_progress is not None:
        call_options["on_member_progress"] = on_member_progress
    result = await caller(envelope, **call_options)
    response = result.get("response")
    errors = list(result.get("errors") or [])
    result_status = result.get("status")
    if result_status == "ok":
        errors += validate_turn(stage, response)
    status = "ok" if result_status == "ok" and not errors else (
        "disagreement" if result_status == "disagreement" else
        "unavailable" if result_status == "unavailable" else "invalid"
    )
    turn_finished_at = datetime.now(timezone.utc).isoformat()
    session["turns"].append({
        "stage": stage,
        "started_at": turn_started_at,
        "finished_at": turn_finished_at,
        "question_purpose": question_purpose,
        "request_envelope": envelope,
        "status": status,
        "response": deepcopy(response),
        "errors": errors,
        "diagnostic": deepcopy(result.get("diagnostic") or {}),
        "member_results": deepcopy(result.get("member_results") or []),
        "consensus": deepcopy(result.get("consensus")),
        "context_policy": deepcopy(envelope.get("context_policy")),
    })
    session["last_question_purpose"] = question_purpose

    if status == "disagreement":
        session["magi_disagreement"] = deepcopy(result.get("consensus"))
        session["user_question"] = (
            "複数のMAGI解釈が分かれました。求めている結果をもう少し具体的に教えてください。"
        )
        session.update(
            status="waiting_user",
            next_step="magi_disagreement_requires_clarification",
        )
        return None
    if status != "ok":
        session.update(status="stopped", next_step="magi_" + status)
        return None
    session["magi_disagreement"] = None
    return response
