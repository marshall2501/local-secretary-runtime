"""Async RITSUKO <-> MAGI execution path.

This is the active provider path for the current Core experiment.  The older
synchronous implementation in magi_dialogue remains temporarily for regression
tests and legacy callers, but new network execution must use this module.

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
from .settings import (
    DEFAULT_RETRY_HTTP_CODES,
    DEFAULT_RETRY_WITHIN_TURN,
    DEFAULT_TIMEOUT_SECONDS,
)
from integrations.llm.ollama_runtime import configured_magi_num_predict, normalize_context_tokens, normalize_magi_num_predict
from .protocol import default_resource_catalog

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
    schema = _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "messages": [
            {"role": "system", "content": SYSTEM},
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
        errors = validate_turn(stage, data)
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
    schema = _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA
    endpoint = str(
        base_url or os.environ.get("OPENAI_BASE_URL") or OPENAI_DEFAULT_BASE_URL
    ).strip().rstrip("/")
    payload = {
        "model": model,
        "store": False,
        "instructions": SYSTEM,
        "input": json.dumps(envelope, ensure_ascii=False),
        "max_output_tokens": 1600,
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
        errors = validate_turn(stage, data)
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
    schema = _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA
    endpoint = str(
        base_url or os.environ.get("GEMINI_BASE_URL") or GEMINI_DEFAULT_BASE_URL
    ).strip().rstrip("/")
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{
            "role": "user",
            "parts": [{"text": json.dumps(envelope, ensure_ascii=False)}],
        }],
        "generationConfig": {
            "maxOutputTokens": 1600,
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
        errors = validate_turn(stage, data)
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
    if context_policy:
        envelope["context_policy"] = deepcopy(context_policy)
    if on_turn_start is not None:
        on_turn_start(turn_number)

    active_specs = (
        member_specs_override
        if member_specs_override is not None
        else session.get("member_specs")
    )
    result = await caller(
        envelope,
        model=session.get("model") or "",
        timeout=timeout,
        member_specs=active_specs,
    )
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
        "context_policy": deepcopy(context_policy) if context_policy else None,
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


async def _apply_detail_async(
    session: dict,
    response: dict,
    *,
    timeout: float,
    purpose: str,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
    member_specs_override: list[dict] | None = None,
    context_policy: dict | None = None,
) -> dict:
    session["detail"] = deepcopy(response)
    session["user_question"] = None
    state = response["state"]

    if _stop_between_turns(session, stop_requested):
        return session

    if state == "READY" and not session["observations"] and purpose != "review_or_repair":
        prompt = _compose_question(
            session,
            "review_or_repair",
            issue="新しいObservationがないのにREADYとなったため、answer_candidateが実回答か作業予定かを再確認する",
        )
        reviewed = await _send_async(
            session,
            "analyze",
            "review_or_repair",
            prompt,
            timeout=timeout,
            caller=caller,
            stop_requested=stop_requested,
            on_turn_start=on_turn_start,
            member_specs_override=member_specs_override,
            context_policy=context_policy,
        )
        if reviewed is None:
            return session
        return await _apply_detail_async(
            session,
            reviewed,
            timeout=timeout,
            purpose="review_or_repair",
            caller=caller,
            stop_requested=stop_requested,
            on_turn_start=on_turn_start,
            member_specs_override=member_specs_override,
            context_policy=context_policy,
        )

    if state == "NEED_INFORMATION":
        requests = response["information_requests"]
        if (
            any(item.get("source") == "user" for item in requests)
            and purpose != "review_or_repair"
            and not session.get("user_source_reviewed")
            and len(session["turns"]) < _turn_limit(session)
        ):
            session["user_source_reviewed"] = True
            prompt = _compose_question(
                session,
                "review_or_repair",
                issue="source=userが提案された。既存のPKB / task_history / files / web等で代替できないblocking情報だけuser要求として残す",
            )
            reviewed = await _send_async(
                session,
                "analyze",
                "review_or_repair",
                prompt,
                timeout=timeout,
                caller=caller,
                stop_requested=stop_requested,
                on_turn_start=on_turn_start,
                member_specs_override=member_specs_override,
                context_policy=context_policy,
            )
            if reviewed is None:
                return session
            return await _apply_detail_async(
                session,
                reviewed,
                timeout=timeout,
                purpose="review_or_repair",
                caller=caller,
                stop_requested=stop_requested,
                on_turn_start=on_turn_start,
                member_specs_override=member_specs_override,
                context_policy=context_policy,
            )

        empty_finance_answer = _grounded_empty_finance_answer(session, requests)
        if empty_finance_answer is not None:
            session["detail"] = {
                "understood_request": str(
                    response.get("understood_request")
                    or (session.get("classification") or {}).get("understood_request")
                    or ""
                ),
                "state": "READY",
                "reason": (
                    "verified Finance Observationで対象範囲の該当明細が0件と確認されたため、"
                    "同じFinance readを繰り返さず、その不足を回答する"
                ),
                "information_requests": [],
                "question_for_user": None,
                "answer_candidate": empty_finance_answer,
                "knowledge_candidate": None,
                "action_candidate": None,
            }
            session["pending_requests"] = []
            session["ritsuko_grounded_fallback"] = {
                "kind": "verified_empty_finance",
                "source": "finance",
                "result_count": 0,
            }
            session.update(
                status="candidate_ready",
                next_step="review_answer_candidate",
            )
            return session

        latest_verified_pkb = _latest_verified_pkb_observation(session)
        if latest_verified_pkb is not None and requests:
            all_pkb = all(item.get("source") == "pkb" for item in requests)
            all_user = all(item.get("source") == "user" for item in requests)
            if purpose == "review_or_repair" and all_user:
                session["pending_requests"] = [
                    {
                        "request_id": (
                            f"REQ-{session['task_id'][:8]}-{len(session['turns']):02d}-{i:02d}"
                        ),
                        **deepcopy(item),
                    }
                    for i, item in enumerate(requests, 1)
                ]
                session["user_question"] = "確認したいこと: " + " / ".join(
                    item["what"] for item in requests
                )
                session.update(
                    status="waiting_user",
                    next_step="ask_user_for_information",
                )
                return session
            if all_pkb:
                if purpose != "review_or_repair" and len(session["turns"]) < _turn_limit(session):
                    prompt = _compose_question(
                        session,
                        "review_or_repair",
                        issue=(
                            "verified PKB Observation直後に同じPKB sourceが再要求された。"
                            "言い換えで同一事実を再読しない。直前のreadで未確認だったblocking事実なら"
                            "source=userへ切り替え、別の事実なら何が異なるかを明確にする"
                        ),
                    )
                    reviewed = await _send_async(
                        session,
                        "analyze",
                        "review_or_repair",
                        prompt,
                        timeout=timeout,
                        caller=caller,
                        stop_requested=stop_requested,
                        on_turn_start=on_turn_start,
                        member_specs_override=member_specs_override,
                        context_policy=context_policy,
                    )
                    if reviewed is None:
                        return session
                    return await _apply_detail_async(
                        session,
                        reviewed,
                        timeout=timeout,
                        purpose="review_or_repair",
                        caller=caller,
                        stop_requested=stop_requested,
                        on_turn_start=on_turn_start,
                        member_specs_override=member_specs_override,
                        context_policy=context_policy,
                    )
                return _wait_for_user_after_exhausted_pkb(session, requests)

        signatures = _request_signatures(requests)
        repeated = bool(session["observations"]) and bool(signatures) and all(
            signature in session["previous_request_signatures"]
            for signature in signatures
        )
        if repeated:
            if purpose != "review_or_repair" and len(session["turns"]) < _turn_limit(session):
                prompt = _compose_question(
                    session,
                    "review_or_repair",
                    issue="Observation追加後も前回と同じ情報要求が返った。Observation不足の具体点を示すか、別の次手へ修正する",
                )
                reviewed = await _send_async(
                    session,
                    "analyze",
                    "review_or_repair",
                    prompt,
                    timeout=timeout,
                    caller=caller,
                    stop_requested=stop_requested,
                    on_turn_start=on_turn_start,
                    member_specs_override=member_specs_override,
                    context_policy=context_policy,
                )
                if reviewed is None:
                    return session
                return await _apply_detail_async(
                    session,
                    reviewed,
                    timeout=timeout,
                    purpose="review_or_repair",
                    caller=caller,
                    stop_requested=stop_requested,
                    on_turn_start=on_turn_start,
                    member_specs_override=member_specs_override,
                    context_policy=context_policy,
                )
            session.update(status="stopped", next_step="repeated_request_without_progress")
            return session

        for signature in signatures:
            if signature not in session["previous_request_signatures"]:
                session["previous_request_signatures"].append(signature)
        session["pending_requests"] = [
            {
                "request_id": (
                    f"REQ-{session['task_id'][:8]}-{len(session['turns']):02d}-{i:02d}"
                ),
                **deepcopy(item),
            }
            for i, item in enumerate(requests, 1)
        ]
        if len(session["turns"]) >= _turn_limit(session):
            session.update(
                status="stopped",
                next_step="max_turns_reached_with_pending_information",
            )
        elif requests and all(item.get("source") == "user" for item in requests):
            session["user_question"] = "確認したいこと: " + " / ".join(
                item["what"] for item in requests
            )
            session.update(status="waiting_user", next_step="ask_user_for_information")
        else:
            session.update(
                status="waiting_information",
                next_step="review_information_requests",
            )
    elif state == "NEED_CLARIFICATION":
        if len(session["turns"]) >= _turn_limit(session):
            session.update(
                status="stopped",
                next_step="max_turns_reached_with_user_question",
            )
        else:
            session["user_question"] = response.get("question_for_user")
            session.update(status="waiting_user", next_step="consider_user_question")
    elif state in {"ACTION_PROPOSAL", "KNOWLEDGE_CANDIDATE"}:
        session.update(status="proposal_ready", next_step="review_proposal")
    elif state == "READY":
        session.update(status="candidate_ready", next_step="review_answer_candidate")
    else:
        session.update(status="stopped", next_step="unable")
    return session


async def _advance_async(
    session: dict,
    *,
    timeout: float,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
    member_specs_override: list[dict] | None = None,
    context_policy: dict | None = None,
) -> dict:
    if _stop_between_turns(session, stop_requested):
        return session
    purpose = _select_question_purpose(session)
    prompt = _compose_question(session, purpose)
    response = await _send_async(
        session,
        "analyze",
        purpose,
        prompt,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
        member_specs_override=member_specs_override,
        context_policy=context_policy,
    )
    if response is None:
        return session
    return await _apply_detail_async(
        session,
        response,
        timeout=timeout,
        purpose=purpose,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
        member_specs_override=member_specs_override,
        context_policy=context_policy,
    )


async def start_dialogue_async(
    user_raw: str,
    *,
    model: str = "",
    member_specs: list[dict] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
    task_id: str | None = None,
) -> dict:
    specs = _normalized_member_specs(member_specs, model)
    from uuid import uuid4

    session = {
        "task_id": str(task_id or uuid4()),
        "user_raw": user_raw.strip(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "magi_mode": "weighted_panel_async",
        "member_specs": specs,
        "status": "running",
        "next_step": "classify",
        "classification": None,
        "detail": None,
        "observations": [],
        "pending_requests": [],
        "previous_request_signatures": [],
        "conversation_context": [],
        "user_question": None,
        "magi_disagreement": None,
        "user_source_reviewed": False,
        "last_question_purpose": None,
        "turns": [],
        "turn_limit": MAX_TURNS,
        "legacy_router_used": False,
        "tool_read_executed": False,
    }
    if not session["user_raw"]:
        session.update(status="stopped", next_step="invalid_input")
        return session
    if not [spec for spec in specs if spec["enabled"]]:
        session.update(status="stopped", next_step="no_magi_member")
        return session

    classification = await _send_async(
        session,
        "classify",
        "classify",
        CLASSIFY_QUESTION,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
    )
    if classification is None:
        return session
    session["classification"] = deepcopy(classification)
    if classification["multiple_requests"]:
        session.update(status="stopped", next_step="multiple_requests_detected")
        return session
    return await _advance_async(
        session,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
    )


async def continue_with_observation_async(
    session: dict,
    observation_text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
) -> dict:
    updated = deepcopy(session)
    if updated.get("status") != "waiting_information":
        raise ValueError("not_waiting_for_information")
    if not isinstance(observation_text, str) or not observation_text.strip():
        raise ValueError("empty_observation")
    if len(updated["turns"]) >= _turn_limit(updated):
        updated.update(status="stopped", next_step="max_turns_reached")
        return updated

    updated["observations"].append({
        "source": "manual_test_input",
        "verified": False,
        "text": observation_text.strip()[:4000],
        "responds_to": [x["request_id"] for x in updated["pending_requests"]],
    })
    updated["pending_requests"] = []
    updated["status"] = "running"
    updated["next_step"] = "evaluate_observation"
    return await _advance_async(
        updated,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
    )


def _private_observation_local_scope(
    session: dict,
) -> tuple[list[dict] | None, dict | None]:
    """Keep later turns local while verified private/sensitive Observations exist."""
    private_sources = sorted({
        str(item.get("source") or "")
        for item in (session.get("observations") or [])
        if isinstance(item, dict)
        and item.get("verified") is True
        and item.get("confidentiality") in {"private", "sensitive"}
    })
    if not private_sources:
        return None, None

    local_specs = [
        deepcopy(spec)
        for spec in (session.get("member_specs") or [])
        if spec.get("enabled") and spec.get("provider") == "ollama"
    ]
    withheld = [
        {
            "name": spec.get("name"),
            "provider": spec.get("provider"),
            "model": spec.get("model"),
        }
        for spec in (session.get("member_specs") or [])
        if spec.get("enabled") and spec.get("provider") != "ollama"
    ]
    read_private_sources = {
        source
        for source in private_sources
        if source in {"pkb", "finance", "files"}
    }
    # Preserve the already-accepted PKB mode when later private Observations are
    # workflow evidence (Proposal Review / Memory Intake) rather than a newly
    # acquired private read Source.
    pkb_only_read_context = read_private_sources == {"pkb"}
    mode = (
        "local_only_private_pkb"
        if pkb_only_read_context
        else "local_only_private_observation"
    )
    reason = (
        "verified_private_pkb_observation"
        if pkb_only_read_context
        else "verified_private_or_sensitive_observation"
    )
    base = {
        "mode": mode,
        "reason": reason,
        "private_sources": private_sources,
        "withheld_members": withheld,
    }
    if not local_specs:
        session["cloud_context_gate"] = {
            **deepcopy(base),
            "status": "blocked_no_local_member",
        }
        session.update(
            status="stopped",
            next_step="cloud_context_gate_no_local_member",
        )
        return [], None

    session["cloud_context_gate"] = {
        **deepcopy(base),
        "status": "applied",
    }
    return local_specs, base


def _private_pkb_local_scope(session: dict):
    """Compatibility alias for older tests/callers."""
    return _private_observation_local_scope(session)


async def continue_with_verified_observations_async(
    session: dict,
    observations: list[dict],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
) -> dict:
    """Continue from one bounded batch of RITSUKO-verified read Observations."""
    updated = deepcopy(session)
    if updated.get("status") != "waiting_information":
        raise ValueError("not_waiting_for_information")
    if not isinstance(observations, list) or not observations:
        raise ValueError("verified_observations_required")
    if len(updated["turns"]) >= _turn_limit(updated):
        updated.update(status="stopped", next_step="max_turns_reached")
        return updated

    safe_observations = []
    resolved_ids: set[str] = set()
    for observation in observations[:12]:
        if not isinstance(observation, dict):
            raise ValueError("invalid_observation")
        if (
            observation.get("source") not in {"pkb", "web", "finance"}
            or observation.get("verified") is not True
        ):
            raise ValueError("verified_read_observation_required")
        text_value = str(observation.get("text") or "").strip()
        if not text_value:
            raise ValueError("empty_observation")
        safe = deepcopy(observation)
        safe["text"] = text_value[:4000]
        if isinstance(safe.get("evidence_preview"), list):
            safe["evidence_preview"] = safe["evidence_preview"][:12]
        safe["responds_to"] = [
            str(value)
            for value in (safe.get("responds_to") or [])
            if str(value).strip()
        ][:20]
        resolved_ids.update(safe["responds_to"])
        safe_observations.append(safe)

    updated["observations"].extend(safe_observations)
    updated["pending_requests"] = [
        item
        for item in (updated.get("pending_requests") or [])
        if not (
            isinstance(item, dict)
            and str(item.get("request_id") or "") in resolved_ids
        )
    ]
    updated["tool_read_executed"] = True
    updated["status"] = "running"
    updated["next_step"] = "evaluate_observation"

    local_specs, context_policy = _private_observation_local_scope(updated)
    if local_specs == []:
        return updated
    return await _advance_async(
        updated,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
        member_specs_override=local_specs,
        context_policy=context_policy,
    )


async def continue_with_verified_observation_async(
    session: dict,
    observation: dict,
    **kwargs,
) -> dict:
    """Compatibility wrapper for the former single-PKB Observation API."""
    return await continue_with_verified_observations_async(
        session,
        [observation],
        **kwargs,
    )


async def continue_with_proposal_review_async(
    session: dict,
    observation: dict,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
) -> dict:
    """Re-evaluate a user-approved proposal review before RITSUKO finalizes."""
    updated = deepcopy(session)
    if updated.get("status") != "proposal_ready":
        raise ValueError("not_waiting_for_proposal_review")
    if not isinstance(observation, dict):
        raise ValueError("invalid_review_observation")
    if observation.get("source") not in {"proposal_review", "memory_intake"}:
        raise ValueError("invalid_review_observation_source")
    if observation.get("verified") is not True:
        raise ValueError("verified_review_observation_required")
    text = str(observation.get("text") or "").strip()
    if not text:
        raise ValueError("empty_review_observation")

    safe_observation = deepcopy(observation)
    safe_observation["text"] = text[:4000]
    safe_observation["responds_to"] = [
        str(value) for value in (safe_observation.get("responds_to") or [])
    ][:20]
    memory_summary = safe_observation.get("memory_intake")
    if isinstance(memory_summary, dict):
        memory_summary["receipts"] = [
            deepcopy(item)
            for item in (memory_summary.get("receipts") or [])
            if isinstance(item, dict)
        ][:100]

    _extend_turn_limit_for_user_resume(updated)
    updated["observations"].append(safe_observation)
    updated["status"] = "running"
    updated["next_step"] = "evaluate_review_result"

    local_specs, context_policy = _private_observation_local_scope(updated)
    if local_specs == []:
        return updated

    prompt = _compose_question(updated, "evaluate_review_result")
    response = await _send_async(
        updated,
        "analyze",
        "evaluate_review_result",
        prompt,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
        member_specs_override=local_specs,
        context_policy=context_policy,
    )
    if response is None:
        return updated

    updated["post_review_evaluation"] = deepcopy(response)
    updated.update(
        status="review_evaluated",
        next_step="ritsuko_finalize_review",
    )
    return updated


async def continue_with_user_clarification_async(
    session: dict,
    user_text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
) -> dict:
    updated = deepcopy(session)
    if updated.get("status") != "waiting_user":
        raise ValueError("not_waiting_for_user")
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("empty_user_clarification")

    text = user_text.strip()[:4000]
    updated.setdefault("conversation_context", []).append({
        "role": "user",
        "text": text,
    })
    updated["conversation_context"] = updated["conversation_context"][-4:]
    updated["user_question"] = None
    updated["magi_disagreement"] = None
    updated["status"] = "running"
    _extend_turn_limit_for_user_resume(updated)

    local_specs, context_policy = _private_observation_local_scope(updated)
    if local_specs == []:
        return updated

    if updated.get("classification") is None:
        updated["next_step"] = "classify_with_context"
        classification = await _send_async(
            updated,
            "classify",
            "classify",
            CLASSIFY_QUESTION,
            timeout=timeout,
            caller=caller,
            stop_requested=stop_requested,
            on_turn_start=on_turn_start,
            member_specs_override=local_specs,
            context_policy=context_policy,
        )
        if classification is None:
            return updated
        updated["classification"] = deepcopy(classification)
        if classification["multiple_requests"]:
            updated.update(status="stopped", next_step="multiple_requests_detected")
            return updated
        return await _advance_async(
            updated,
            timeout=timeout,
            caller=caller,
            stop_requested=stop_requested,
            on_turn_start=on_turn_start,
            member_specs_override=local_specs,
            context_policy=context_policy,
        )

    responds_to = [
        item["request_id"]
        for item in updated.get("pending_requests") or []
        if isinstance(item, dict) and item.get("request_id")
    ]
    updated["observations"].append({
        "source": "user_clarification",
        "verified": False,
        "text": text,
        "responds_to": responds_to,
    })
    updated["pending_requests"] = []
    updated["next_step"] = "evaluate_user_clarification"
    prompt = _compose_question(updated, "evaluate_observation")
    response = await _send_async(
        updated,
        "analyze",
        "evaluate_observation",
        prompt,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
        member_specs_override=local_specs,
        context_policy=context_policy,
    )
    if response is None:
        return updated
    return await _apply_detail_async(
        updated,
        response,
        timeout=timeout,
        purpose="evaluate_observation",
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
        member_specs_override=local_specs,
        context_policy=context_policy,
    )
