"""RITSUKO-owned request/response contract for MAGI Protocol v1.

RITSUKO creates every request envelope and validates every response. This
module intentionally contains no routing to PKB/Web and never executes tools.
"""
from __future__ import annotations

from copy import deepcopy

PROTOCOL_VERSION = "1.0"
MAGI_MEMBERS = ("MELCHIOR", "BALTHASAR", "CASPER")

INTENT_TYPES = (
    "information_lookup", "knowledge_statement", "explicit_correction",
    "comparison", "investigation", "action_request", "schedule_or_reminder",
    "conversation", "unknown", "other",
)
TARGET_STATUSES = ("resolved", "partial", "unresolved", "not_applicable")
REQUESTED_RESULT_TYPES = (
    "information", "registration_candidate", "correction_candidate",
    "comparison_result", "investigation_result", "action_result",
    "schedule_result", "explanation", "conversation_response", "unknown", "other",
)
UNDERSTANDING_STATUSES = ("complete", "partial", "uninterpretable")
INFORMATION_SUFFICIENCY_STATUSES = ("sufficient", "insufficient", "unknown")
INFORMATION_SOURCES = (
    "current_context", "pkb", "task_history", "web", "finance", "files",
    "external_service", "pc_observation", "user", "unknown",
)
RECOMMENDED_NEXT_TYPES = (
    "respond", "request_information", "ask_user", "propose_action",
    "wait", "no_action", "unable",
)

SYSTEM_INSTRUCTION = """あなたは Personal Local Secretary AI の分析・判断支援を担当する
MAGI System の1メンバーです。

member_name は今回の入力で指定されます。
あなたは最終決定者ではありません。

ユーザーと直接対話してTaskを制御するのはRITSUKOです。
あなたはRITSUKOが作成した依頼データだけを分析し、結果をRITSUKOへ返します。
Task状態変更、Tool実行、PKB/Web読取、完了判定を自分で実行してはいけません。

ユーザー原文、Task Context、Observation、利用可能な情報源を分析し、
指定されたJSON契約だけで分析結果を返してください。

必ず、意図、対象、要求結果、入力理解度、情報十分性、不足情報、
候補情報源、知識候補、曖昧箇所、解釈不能箇所、次の提案を評価してください。
不足する事実を推測で補完してはいけません。
ユーザーが指す対象や求める結果を理解できることと、その対象の属性・
現状・履歴をまだ知らないことを区別してください。
情報が不足している場合は、何が必要か、どこから得られそうか、
なぜ必要かをinformation_requestsで具体化してください。
情報源を要求する場合はlikely_information_sourcesにも同じ候補を挙げ、
各information_requestに空でない一意のrequest_id（REQ-001等）を付けてください。
利用可能な情報源から取得できそうな情報は先にRITSUKOへ要求してください。
ユーザーへの質問は、情報源で取得できず回答に必要な確認がある場合に提案してください。
「最新」などの相対的な表現は、まず調査時点での情報取得を検討し、
情報源を確認する前にユーザーへ定義の確認を求める必要があるか吟味してください。

PKBはユーザー本人の対象、属性、関係、現在状態、過去状態、出来事、
履歴、情報源を保持するPersonal Knowledge Baseです。
PKB全件は入力されていません。必要なら具体的な情報要求を返してください。

指定されたJSON以外の文章を返してはいけません。"""

REQUIRED_ANALYSIS = {
    "intent": "ユーザーは何をしたいのか",
    "target": "何についての依頼なのか",
    "requested_result": "ユーザーは最終的に何を知りたい／登録したい／訂正したい／比較したい／実行したいのか",
    "understanding": "入力の意味を十分理解できたか。曖昧または解釈不能なら具体的な箇所を示す",
    "information_sufficiency": "現在与えられた情報だけで回答・提案・判断に十分か",
    "missing_information": "不足している情報は具体的に何か",
    "likely_information_sources": "不足情報はどの情報源に存在する可能性があるか",
    "knowledge_candidates": "ユーザーが本人知識を提示している場合の候補。登録確定ではない",
    "recommended_next": "現在の分析からRITSUKOが次に検討すべきこと",
}

ALLOWED_VALUES = {
    "intent.type": list(INTENT_TYPES),
    "target.status": list(TARGET_STATUSES),
    "requested_result.type": list(REQUESTED_RESULT_TYPES),
    "understanding.status": list(UNDERSTANDING_STATUSES),
    "information_sufficiency.status": list(INFORMATION_SUFFICIENCY_STATUSES),
    "information_source": list(INFORMATION_SOURCES),
    "recommended_next.type": list(RECOMMENDED_NEXT_TYPES),
}

REQUIRED_OUTPUT_SHAPE = {
    "protocol_version": "string",
    "message_type": "analysis_result",
    "task_id": "string",
    "cycle": "integer",
    "magi_member": "string",
    "analysis": {
        "intent": {"type": "allowed:intent.type", "description": "string"},
        "target": {"status": "allowed:target.status", "items": "array"},
        "requested_result": {"type": "allowed:requested_result.type", "description": "string|null"},
        "understanding": {"status": "allowed:understanding.status", "ambiguities": "array", "uninterpretable_parts": "array"},
        "information_sufficiency": {"status": "allowed:information_sufficiency.status", "missing_information": "array"},
        "likely_information_sources": "array",
        "knowledge_candidates": "array",
        "information_requests": "array",
        "uncertainties": "array",
    },
    "recommended_next": {
        "type": "allowed:recommended_next.type",
        "reason": "string",
        "answer_candidate": "string|null",
        "action_candidate": "object|null",
        "question_for_user": "string|null",
    },
}

MAGI_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["protocol_version","message_type","task_id","cycle","magi_member","analysis","recommended_next"],
    "properties": {
        "protocol_version": {"type":"string","const":PROTOCOL_VERSION},
        "message_type": {"type":"string","const":"analysis_result"},
        "task_id": {"type":"string"},
        "cycle": {"type":"integer","minimum":1},
        "magi_member": {"type":"string","enum":list(MAGI_MEMBERS)},
        "analysis": {
            "type":"object","additionalProperties":False,
            "required":["intent","target","requested_result","understanding","information_sufficiency","likely_information_sources","knowledge_candidates","information_requests","uncertainties"],
            "properties": {
                "intent": {
                    "type":"object","additionalProperties":False,"required":["type","description"],
                    "properties":{"type":{"type":"string","enum":list(INTENT_TYPES)},"description":{"type":"string"}},
                },
                "target": {
                    "type":"object","additionalProperties":False,"required":["status","items"],
                    "properties":{
                        "status":{"type":"string","enum":list(TARGET_STATUSES)},
                        "items":{"type":"array","items":{
                            "type":"object","additionalProperties":False,
                            "required":["raw_reference","interpreted_as"],
                            "properties":{"raw_reference":{"type":"string"},"interpreted_as":{"type":"string"}},
                        }},
                    },
                },
                "requested_result": {
                    "type":"object","additionalProperties":False,"required":["type","description"],
                    "properties":{"type":{"type":"string","enum":list(REQUESTED_RESULT_TYPES)},"description":{"type":["string","null"]}},
                },
                "understanding": {
                    "type":"object","additionalProperties":False,"required":["status","ambiguities","uninterpretable_parts"],
                    "properties":{
                        "status":{"type":"string","enum":list(UNDERSTANDING_STATUSES)},
                        "ambiguities":{"type":"array","items":{
                            "type":"object","additionalProperties":False,
                            "required":["part","problem","possible_interpretations","blocking"],
                            "properties":{
                                "part":{"type":"string"},"problem":{"type":"string"},
                                "possible_interpretations":{"type":"array","items":{"type":"string"}},
                                "blocking":{"type":"boolean"},
                            },
                        }},
                        "uninterpretable_parts":{"type":"array","items":{
                            "type":"object","additionalProperties":False,
                            "required":["part","reason","blocking"],
                            "properties":{"part":{"type":"string"},"reason":{"type":"string"},"blocking":{"type":"boolean"}},
                        }},
                    },
                },
                "information_sufficiency": {
                    "type":"object","additionalProperties":False,"required":["status","missing_information"],
                    "properties":{
                        "status":{"type":"string","enum":list(INFORMATION_SUFFICIENCY_STATUSES)},
                        "missing_information":{"type":"array","items":{
                            "type":"object","additionalProperties":False,
                            "required":["description","reason"],
                            "properties":{"description":{"type":"string"},"reason":{"type":"string"}},
                        }},
                    },
                },
                "likely_information_sources": {"type":"array","items":{
                    "type":"object","additionalProperties":False,"required":["source","reason"],
                    "properties":{"source":{"type":"string","enum":list(INFORMATION_SOURCES)},"reason":{"type":"string"}},
                }},
                "knowledge_candidates": {"type":"array","items":{
                    "type":"object","additionalProperties":False,
                    "required":["subject","relation_or_property","value","statement_kind","temporal_hint","source"],
                    "properties":{
                        "subject":{"type":["string","null"]},
                        "relation_or_property":{"type":["string","null"]},
                        "value":{"type":["string","number","boolean","null"]},
                        "statement_kind":{"type":["string","null"]},
                        "temporal_hint":{"type":["string","null"]},
                        "source":{"type":["string","null"]},
                    },
                }},
                "information_requests": {"type":"array","items":{
                    "type":"object","additionalProperties":False,
                    "required":["request_id","source_preferences","request","requested_facts","reason","blocking"],
                    "properties":{
                        "request_id":{"type":"string","minLength":1},
                        "source_preferences":{"type":"array","items":{"type":"string","enum":list(INFORMATION_SOURCES)}},
                        "request":{"type":"string"},
                        "requested_facts":{"type":"array","items":{"type":"string"}},
                        "reason":{"type":"string"},
                        "blocking":{"type":"boolean"},
                    },
                }},
                "uncertainties":{"type":"array","items":{"type":"string"}},
            },
        },
        "recommended_next": {
            "type":"object","additionalProperties":False,
            "required":["type","reason","answer_candidate","action_candidate","question_for_user"],
            "properties":{
                "type":{"type":"string","enum":list(RECOMMENDED_NEXT_TYPES)},
                "reason":{"type":"string"},
                "answer_candidate":{"type":["string","null"]},
                "action_candidate":{"type":["object","null"]},
                "question_for_user":{"type":["string","null"]},
            },
        },
    },
}

def default_resource_catalog() -> dict:
    return {
        "pkb": {
            "available": True,
            "access": "read_only_via_ritsuko",
            "description": "ユーザー本人に関する構造化知識",
            "contains": ["対象","属性","関係","現在状態","過去状態","出来事","履歴","情報源"],
            "supports": ["対象検索","現在値検索","履歴検索","時点検索","関係検索","情報源確認"],
        },
        "task_history": {
            "available": False,
            "access": "not_connected_to_state_driven_loop",
        },
        "web": {
            "available": True,
            "access": "bounded_read_only_via_ritsuko",
            "confidentiality": "public",
        },
        "finance": {
            "available": True,
            "access": "bounded_read_only_via_ritsuko",
            "confidentiality": "private",
        },
        "files": {
            "available": False,
            "access": "not_implemented",
            "description": "Files read Capabilityとallowlistは未実装",
        },
        "external_service": {"available": False, "access": "permission_dependent"},
        "pc_observation": {"available": False, "access": "permission_dependent"},
        "user": {"available": True, "access": "ask_user"},
    }

def build_request_envelope(*, task_id: str, cycle: int, member_name: str, user_raw: str,
                           task_context: dict | None = None,
                           resource_catalog: dict | None = None,
                           observations: list | None = None) -> dict:
    if member_name not in MAGI_MEMBERS:
        raise ValueError("unknown_magi_member")
    if type(cycle) is not int or cycle < 1:
        raise ValueError("invalid_cycle")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("invalid_task_id")
    if not isinstance(user_raw, str) or not user_raw.strip():
        raise ValueError("invalid_user_input")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": "analyze" if cycle == 1 else "continue_analysis",
        "task_id": task_id,
        "cycle": cycle,
        "magi_member": {"name": member_name},
        "user_input": {"raw": user_raw},
        "task_context": deepcopy(task_context or {
            "status":"received","goal":None,"previous_user_messages":[],
            "previous_actions":[],"previous_results":[],
        }),
        "resource_catalog": deepcopy(resource_catalog or default_resource_catalog()),
        "observations": deepcopy(observations or []),
        "request_contract": {
            "instruction": "user_input.rawと現在のTask Context / Observationを分析し、required_analysisの全項目を判断した上でresponse_contractに従って返すこと。",
            "required_analysis": deepcopy(REQUIRED_ANALYSIS),
            "do_not_omit_required_fields": True,
            "use_null_or_empty_array_when_not_applicable": True,
            "do_not_invent_missing_facts": True,
        },
        "response_contract": {
            "format":"json_only",
            "schema_name":"magi_analysis_v1",
            "allowed_values":deepcopy(ALLOWED_VALUES),
            "required_output_shape":deepcopy(REQUIRED_OUTPUT_SHAPE),
        },
    }

def _exact_keys(value: object, expected: set[str], path: str, errors: list[str]) -> dict | None:
    if not isinstance(value, dict):
        errors.append(path + ":object_required")
        return None
    keys=set(value)
    if keys != expected:
        missing=sorted(expected-keys)
        extra=sorted(keys-expected)
        if missing:
            errors.append(path + ":missing=" + ",".join(missing))
        if extra:
            errors.append(path + ":unexpected=" + ",".join(extra))
    return value

def validate_analysis_result(response: object, request_envelope: dict) -> list[str]:
    errors: list[str] = []
    top=_exact_keys(response,{"protocol_version","message_type","task_id","cycle","magi_member","analysis","recommended_next"},"$",errors)
    if top is None:
        return errors
    if top.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("$.protocol_version:mismatch")
    if top.get("message_type") != "analysis_result":
        errors.append("$.message_type:invalid")
    if top.get("task_id") != request_envelope.get("task_id"):
        errors.append("$.task_id:mismatch")
    if top.get("cycle") != request_envelope.get("cycle"):
        errors.append("$.cycle:mismatch")
    if top.get("magi_member") != (request_envelope.get("magi_member") or {}).get("name"):
        errors.append("$.magi_member:mismatch")

    analysis=_exact_keys(top.get("analysis"),{"intent","target","requested_result","understanding","information_sufficiency","likely_information_sources","knowledge_candidates","information_requests","uncertainties"},"$.analysis",errors)
    nxt=_exact_keys(top.get("recommended_next"),{"type","reason","answer_candidate","action_candidate","question_for_user"},"$.recommended_next",errors)
    if analysis is None or nxt is None:
        return errors
    intent=_exact_keys(analysis.get("intent"),{"type","description"},"$.analysis.intent",errors)
    target=_exact_keys(analysis.get("target"),{"status","items"},"$.analysis.target",errors)
    requested=_exact_keys(analysis.get("requested_result"),{"type","description"},"$.analysis.requested_result",errors)
    understanding=_exact_keys(analysis.get("understanding"),{"status","ambiguities","uninterpretable_parts"},"$.analysis.understanding",errors)
    sufficiency=_exact_keys(analysis.get("information_sufficiency"),{"status","missing_information"},"$.analysis.information_sufficiency",errors)

    for obj,key,allowed,path in (
        (intent,"type",INTENT_TYPES,"$.analysis.intent.type"),
        (target,"status",TARGET_STATUSES,"$.analysis.target.status"),
        (requested,"type",REQUESTED_RESULT_TYPES,"$.analysis.requested_result.type"),
        (understanding,"status",UNDERSTANDING_STATUSES,"$.analysis.understanding.status"),
        (sufficiency,"status",INFORMATION_SUFFICIENCY_STATUSES,"$.analysis.information_sufficiency.status"),
        (nxt,"type",RECOMMENDED_NEXT_TYPES,"$.recommended_next.type"),
    ):
        if obj is not None and obj.get(key) not in allowed:
            errors.append(path + ":invalid_enum")

    for obj,key,path in (
        (target,"items","$.analysis.target.items"),
        (understanding,"ambiguities","$.analysis.understanding.ambiguities"),
        (understanding,"uninterpretable_parts","$.analysis.understanding.uninterpretable_parts"),
        (sufficiency,"missing_information","$.analysis.information_sufficiency.missing_information"),
        (analysis,"likely_information_sources","$.analysis.likely_information_sources"),
        (analysis,"knowledge_candidates","$.analysis.knowledge_candidates"),
        (analysis,"information_requests","$.analysis.information_requests"),
        (analysis,"uncertainties","$.analysis.uncertainties"),
    ):
        if obj is not None and not isinstance(obj.get(key),list):
            errors.append(path + ":array_required")

    likely=analysis.get("likely_information_sources")
    if isinstance(likely,list):
        for index,item in enumerate(likely):
            if not isinstance(item,dict) or item.get("source") not in INFORMATION_SOURCES:
                errors.append(f"$.analysis.likely_information_sources[{index}]:invalid_source")
    requests=analysis.get("information_requests")
    if isinstance(requests,list):
        seen:set[str]=set()
        for index,item in enumerate(requests):
            path=f"$.analysis.information_requests[{index}]"
            if not isinstance(item,dict):
                errors.append(path + ":object_required")
                continue
            request_id=item.get("request_id")
            if not isinstance(request_id,str) or not request_id.strip():
                errors.append(path + ".request_id:invalid")
            elif request_id in seen:
                errors.append(path + ".request_id:duplicate")
            else:
                seen.add(request_id)
            sources=item.get("source_preferences")
            if not isinstance(sources,list) or any(source not in INFORMATION_SOURCES for source in sources):
                errors.append(path + ".source_preferences:invalid")
    if not isinstance(nxt.get("reason"),str) or not nxt.get("reason","").strip():
        errors.append("$.recommended_next.reason:required")
    return errors
