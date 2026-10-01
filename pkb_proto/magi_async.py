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
import json
import os
import time
from urllib.parse import quote

import anyio

from .async_transport import (
    AsyncHTTPStatusError,
    AsyncRequestTimeout,
    AsyncTransportError,
    request_json,
)
from .magi_client import OLLAMA
from .magi_dialogue import (
    CLASSIFY_QUESTION,
    MAX_TURNS,
    PROMPT_VERSION,
    SYSTEM,
    _CLASSIFICATION_SCHEMA,
    _DETAIL_SCHEMA,
    _compose_question,
    _extract_gemini_text,
    _extract_openai_output_text,
    _normalized_member_specs,
    _request_signatures,
    _select_question_purpose,
    _stop_between_turns,
    select_weighted_consensus,
    validate_turn,
)
from .magi_settings import DEFAULT_TIMEOUT_SECONDS
from .ollama_runtime import normalize_context_tokens
from .ritsuko_magi_protocol import default_resource_catalog

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _provider_failure(provider: str, exc: Exception) -> dict:
    if isinstance(exc, AsyncRequestTimeout):
        error = "timeout"
        diagnostic = {"provider": provider, "error": error}
    elif isinstance(exc, AsyncHTTPStatusError):
        error = "HTTPError"
        diagnostic = {
            "provider": provider,
            "error": error,
            "http_status": exc.status_code,
        }
    else:
        error = type(exc).__name__
        diagnostic = {"provider": provider, "error": error}
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
) -> dict:
    stage = envelope["stage"]
    schema = _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA
    payload = {
        "model": model,
        "stream": False,
        "format": schema,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(envelope, ensure_ascii=False)},
        ],
        "options": {
            "temperature": 0,
            "num_predict": 1150,
            "num_ctx": normalize_context_tokens(context_window_tokens),
        },
    }
    endpoint = str(base_url or OLLAMA).strip().rstrip("/")
    try:
        outer = await request_json(
            "POST",
            endpoint + "/api/chat",
            headers={"Content-Type": "application/json"},
            json_body=payload,
            timeout=timeout,
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
        AsyncRequestTimeout,
        AsyncHTTPStatusError,
        AsyncTransportError,
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
    credential_env: str | None = None,
) -> dict:
    credential_name = credential_env or "OPENAI_API_KEY"
    api_key = os.environ.get(credential_name, "").strip()
    if not api_key:
        return {
            "status": "unavailable",
            "response": None,
            "errors": ["missing_provider_credential"],
            "diagnostic": {
                "provider": "openai",
                "error": "missing_provider_credential",
                "credential_env": credential_name,
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
        outer = await request_json(
            "POST",
            endpoint + "/responses",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
            },
            json_body=payload,
            timeout=timeout,
        )
        raw = _extract_openai_output_text(outer)
        usage = outer.get("usage") if isinstance(outer, dict) else None
        diagnostic = {
            "provider": "openai",
            "response_status": outer.get("status") if isinstance(outer, dict) else None,
            "response_id": outer.get("id") if isinstance(outer, dict) else None,
            "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
            "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
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
        AsyncRequestTimeout,
        AsyncHTTPStatusError,
        AsyncTransportError,
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
    credential_env: str | None = None,
) -> dict:
    credential_name = credential_env or "GEMINI_API_KEY"
    api_key = os.environ.get(credential_name, "").strip()
    if not api_key:
        return {
            "status": "unavailable",
            "response": None,
            "errors": ["missing_provider_credential"],
            "diagnostic": {
                "provider": "gemini",
                "error": "missing_provider_credential",
                "credential_env": credential_name,
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
                    "mimeType": "application/json",
                    "schema": schema,
                }
            },
        },
    }
    try:
        outer = await request_json(
            "POST",
            endpoint + "/models/" + quote(model, safe="") + ":generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json_body=payload,
            timeout=timeout,
        )
        raw = _extract_gemini_text(outer)
        usage = outer.get("usageMetadata") if isinstance(outer, dict) else None
        diagnostic = {
            "provider": "gemini",
            "prompt_tokens": usage.get("promptTokenCount") if isinstance(usage, dict) else None,
            "candidate_tokens": usage.get("candidatesTokenCount") if isinstance(usage, dict) else None,
            "total_tokens": usage.get("totalTokenCount") if isinstance(usage, dict) else None,
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
        AsyncRequestTimeout,
        AsyncHTTPStatusError,
        AsyncTransportError,
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
        )
    elif provider == "openai":
        result = await _call_openai_guided_async(
            member_envelope,
            model=spec["model"],
            timeout=member_timeout,
            base_url=spec.get("endpoint"),
            credential_env=spec.get("credential_env"),
        )
    elif provider == "gemini":
        result = await _call_gemini_guided_async(
            member_envelope,
            model=spec["model"],
            timeout=member_timeout,
            base_url=spec.get("endpoint"),
            credential_env=spec.get("credential_env"),
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
        "provider": provider,
        "model": spec["model"],
        "weight": spec["weight"],
        "timeout_seconds": spec.get("timeout_seconds"),
        "context_window_tokens": spec.get("context_window_tokens"),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "status": result.get("status"),
        "response": deepcopy(result.get("response")),
        "errors": list(result.get("errors") or []),
        "diagnostic": deepcopy(result.get("diagnostic") or {}),
    }


async def call_guided_panel_async(
    envelope: dict,
    *,
    model: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    member_specs: list[dict] | None = None,
) -> dict:
    """Run enabled provider-independent MAGI members concurrently and cancellably."""
    specs = [
        spec for spec in _normalized_member_specs(member_specs, model)
        if spec["enabled"]
    ]
    member_results: list[dict | None] = [None] * len(specs)

    async def run_one(index: int, spec: dict) -> None:
        try:
            member_results[index] = await _call_panel_member_async(
                spec, envelope, timeout=timeout
            )
        except Exception as exc:
            member_results[index] = {
                "name": spec["name"],
                "profile_id": spec.get("profile_id"),
                "provider": spec["provider"],
                "model": spec["model"],
                "weight": spec["weight"],
                "timeout_seconds": spec.get("timeout_seconds"),
                "status": "unavailable",
                "response": None,
                "errors": [type(exc).__name__],
                "diagnostic": {"error": type(exc).__name__},
            }

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
                    "provider": spec["provider"],
                    "model": spec["model"],
                    "weight": spec["weight"],
                    "timeout_seconds": spec["timeout_seconds"],
                    "context_window_tokens": spec.get("context_window_tokens"),
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
) -> dict | None:
    if len(session["turns"]) >= MAX_TURNS:
        session.update(status="stopped", next_step="max_turns_reached")
        return None
    if _stop_between_turns(session, stop_requested):
        return None

    turn_number = len(session["turns"]) + 1
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
    if on_turn_start is not None:
        on_turn_start(turn_number)

    result = await caller(
        envelope,
        model=session.get("model") or "",
        timeout=timeout,
        member_specs=session.get("member_specs"),
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
    session["turns"].append({
        "stage": stage,
        "question_purpose": question_purpose,
        "request_envelope": envelope,
        "status": status,
        "response": deepcopy(response),
        "errors": errors,
        "diagnostic": deepcopy(result.get("diagnostic") or {}),
        "member_results": deepcopy(result.get("member_results") or []),
        "consensus": deepcopy(result.get("consensus")),
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
        )

    if state == "NEED_INFORMATION":
        requests = response["information_requests"]
        if (
            any(item.get("source") == "user" for item in requests)
            and purpose != "review_or_repair"
            and not session.get("user_source_reviewed")
            and len(session["turns"]) < MAX_TURNS
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
            )

        signatures = _request_signatures(requests)
        repeated = bool(session["observations"]) and bool(signatures) and all(
            signature in session["previous_request_signatures"]
            for signature in signatures
        )
        if repeated:
            if purpose != "review_or_repair" and len(session["turns"]) < MAX_TURNS:
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
        if len(session["turns"]) >= MAX_TURNS:
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
        if len(session["turns"]) >= MAX_TURNS:
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
) -> dict:
    specs = _normalized_member_specs(member_specs, model)
    from uuid import uuid4

    session = {
        "task_id": str(uuid4()),
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
    if len(updated["turns"]) >= MAX_TURNS:
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
    )
