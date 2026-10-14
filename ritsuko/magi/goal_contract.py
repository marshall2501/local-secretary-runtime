"""Versioned MAGI judgment contract for a bounded, public read-only Task.

The LLM proposes meaning and evidence relevance. It never commits Task state,
executes Tools, or vouches for the existence of a Source/Result record.
"""
from __future__ import annotations

VERSION = "goal_loop_v1"

GOAL_SYSTEM = """あなたはPersonal Local Secretary AIのMAGI判断役です。
本人のGoalを理解し、公開・低リスクの一般回答、または許可済み公開Web readの必要性を判断します。
JSON schemaの形式で、respond / request_read / ask_user / wait / unable のいずれかを提案してください。
新規Sourceの取得、Task状態確定、権限変更、Tool実行はあなたにはできません。
取得したResultのIDと検証範囲を必ず区別します。
provider_citedはProviderがURLを報告したことに過ぎず、本文確認済みでも主張の真偽保証でもありません。
現在のWeb readは検索Providerの引用取得のみであり、URL指定によるページ本文の独立取得はできません。
本人が本文を直接確認するよう依頼した場合、検索結果だけで達成済みとせず、非対応なら不足を明示してください。
evidence.basis=insufficientは既存Result IDを含められます（何を得て何が足りないかを示すため）。
evidence.basis=not_requiredではResult IDを含めず、respondにはinsufficientを使わないでください。
本人が公式Source・最新情報・Webでの確認を要求した場合、未取得のままnot_requiredとしてはいけません。
公開Webが必要ならrequest_readを提案してください。許可されていない操作や個人情報のWeb送信を提案しないでください。
十分でない場合は答えを捏造せず、理由と具体的な次の行動を示してください。
出典なしで答えられる一般説明はnot_requiredとし、検索を無理に行わないでください。
返答は指定されたJSONだけにしてください。"""

EVIDENCE = {
    "type": "object", "additionalProperties": False,
    "required": ["basis", "result_ids", "assessment"],
    "properties": {
        "basis": {"type": "string", "enum": ["not_required", "result_refs", "insufficient"]},
        "result_ids": {"type": "array", "items": {"type": "string"}},
        "assessment": {"type": "string"},
    },
}
READ_REQUEST = {
    "type": "object", "additionalProperties": False,
    "required": ["source", "query", "reason"],
    "properties": {
        "source": {"type": "string", "enum": ["web"]},
        "query": {"type": "string"},
        "reason": {"type": "string"},
    },
}
GOAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["goal", "next", "fulfillment", "answer", "evidence",
                 "read_request", "question", "reason"],
    "properties": {
        "goal": {"type": "string"},
        "next": {"type": "string", "enum": [
            "respond", "request_read", "ask_user", "wait", "unable",
        ]},
        "fulfillment": {"type": "string", "enum": [
            "met", "partial", "unmet", "unknown",
        ]},
        "answer": {"type": ["string", "null"]},
        "evidence": EVIDENCE,
        "read_request": {"anyOf": [READ_REQUEST, {"type": "null"}]},
        "question": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
}


def validate_goal_turn(value: object) -> list[str]:
    """Do not trust a provider's schema enforcement or self-assessed success."""
    if not isinstance(value, dict):
        return ["goal:not_object"]
    errors: list[str] = []
    if set(value) != set(GOAL_SCHEMA["required"]):
        errors.append("goal:field_mismatch")
    for key in ("goal", "reason"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            errors.append(key + ":required")
    next_step = value.get("next")
    fulfillment = value.get("fulfillment")
    if next_step not in GOAL_SCHEMA["properties"]["next"]["enum"]:
        errors.append("next:invalid")
    if fulfillment not in GOAL_SCHEMA["properties"]["fulfillment"]["enum"]:
        errors.append("fulfillment:invalid")
    for key in ("answer", "question"):
        if value.get(key) is not None and not isinstance(value[key], str):
            errors.append(key + ":invalid")
    evidence = value.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE["required"]):
        errors.append("evidence:invalid_shape")
        evidence = {}
    basis = evidence.get("basis")
    ids = evidence.get("result_ids")
    if basis not in EVIDENCE["properties"]["basis"]["enum"]:
        errors.append("evidence:invalid_basis")
    if not isinstance(ids, list) or any(
        not isinstance(s, str) or not s.strip() or len(s) > 100 for s in ids
    ) or (isinstance(ids, list) and len(ids) > 12):
        errors.append("evidence:invalid_result_ids")
    elif basis == "not_required" and ids:
        errors.append("evidence:unexpected_refs")
    elif basis == "result_refs" and not ids:
        errors.append("evidence:missing_refs")
    # "insufficient" may cite an already recorded Result while explaining
    # that it does NOT substantiate the Goal (e.g. provider_cited, not fetched).
    # RITSUKO checks every referenced ID against Task-local observations.
    if not isinstance(evidence.get("assessment"), str) or not evidence["assessment"].strip():
        errors.append("evidence:assessment_required")
    req = value.get("read_request")
    if req is not None:
        if (not isinstance(req, dict) or set(req) != set(READ_REQUEST["required"])
                or req.get("source") != "web"
                or any(not isinstance(req.get(k), str) or not req[k].strip()
                       for k in ("query", "reason"))):
            errors.append("read_request:invalid")
    if (next_step == "request_read") != (req is not None):
        errors.append("read_request:state_mismatch")
    if next_step == "respond":
        if fulfillment != "met" or not isinstance(value.get("answer"), str) or not value["answer"].strip():
            errors.append("respond:answer_or_goal_missing")
        if basis == "insufficient":
            errors.append("respond:insufficient_evidence")
    if next_step == "ask_user" and not (
        isinstance(value.get("question"), str) and value["question"].strip()
    ):
        errors.append("ask_user:question_required")
    if next_step != "ask_user" and value.get("question") is not None:
        errors.append("question:unexpected")
    if next_step != "respond" and value.get("answer") is not None:
        errors.append("answer:unexpected")
    return errors
