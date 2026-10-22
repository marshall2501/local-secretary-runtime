"""既存RITSUKOのSource-neutral readに判断を依頼するMAGI契約。

The LLM proposes meaning and evidence relevance. It never commits Task state,
executes Tools, or vouches for the existence of a Source/Result record.
"""
from __future__ import annotations

from ritsuko.application.observation_sources import AUTO_READ_SOURCES, READ_OPERATIONS
from pkb.write_service import KNOWN_PREDICATES

WRITE_OPERATION_ID = "pkb.register_claim"
WRITE_ARGUMENT_KEYS = frozenset({
    "entity_id", "entity_mention", "predicate", "value",
    "evidence_start", "evidence_end",
})

VERSION = "goal_loop_v2"  # Wire format; persisted control_mode remains v1 during migration.
DEFAULT_GOAL_TURN_LIMIT = 6

GOAL_SYSTEM = """あなたはPersonal Local Secretary AIのMAGI判断役です。
本人のGoalを理解し、利用可能な操作から次の一手を提案してください。
重要：本人の原文に「かもしれない」「未確認」「不明」「らしい」などの不確実・推測表現があり、記憶させる対象の事実が未確定なら、PKBに登録する `pkb.register_claim` は提案しないでください。既存Entity IDが確認済みでも、属性名や値が明示されていても同じです。その場合は next=ask_user と question で本人に事実の確定を確認してください。例：「架空GPUの機種はRTX-SYNTH-400かもしれない。」には登録操作ではなく「機種はRTX-SYNTH-400で確定していますか？」と質問してください。原文の一部だけを引用して「かもしれない」を除外しても確定扱いにしないでください。新しい情報の不足と、事実自体の未確定を混同しないでください。
JSON schemaの形式で、invoke / respond / ask_user / wait / unable のいずれかを提案してください。request_readは旧Task互換のみです。
**フィールドの使い分けは必須です**。「answer」はGoalを達成して本人へ最終回答するとき（next=respond）だけ記入します。
- next=invoke（PKBの読取・登録など操作を提案）: answer=null、question=null、read_request=null、operation_requestに操作を書く。説明はreasonへ書く。実行前のため登録済み・完了と書かない。
- next=respond（最終回答）: answerに本人向けの回答を書く、question=null、operation_request=null、read_request=null。未実行の書込みを完了したと主張しない。
- next=ask_user（本人への質問）: questionに質問を書く、answer=null、operation_request=null、read_request=null。
- next=wait / unable: answer=null、question=null、operation_request=null、read_request=null。補足はreasonへ書く。
JSONのanswerフィールドが必須でも、最終回答以外は文字列で埋めず、必ずJSONのnullにしてください。
新規Sourceの取得、Task状態確定、権限変更、Tool実行はあなたにはできません。
取得したResultのIDと検証範囲を必ず区別します。
provider_citedはProviderがURLを報告したことに過ぎず、本文確認済みでも主張の真偽保証でもありません。
WebはProviderの引用検索のみで、URL指定によるページ本文の独立取得はできません。PKBや家計のreadもRITSUKOが許可・実行し、あなたは実施済みと決めつけてはいけません。
本人が本文を直接確認するよう依頼した場合、検索結果だけで達成済みとせず、非対応なら不足を明示してください。
evidence.basis=insufficientは既存Result IDを含められます（何を得て何が足りないかを示すため）。
evidence.basis=not_requiredではResult IDを含めず、respondにはinsufficientを使わないでください。
**PKBへの登録を伴うGoalでは、登録後の最終回答は実際の書込みResultを根拠にしてください。** observationsにsource=pkb_write、verified=true、result_idがある場合、本人の登録Goalを達成したと判断してnext=respondにするなら、evidence.basis=result_refsとし、evidence.result_idsにはその書込みObservationのresult_idをそのまま含めてください。検索だけのResult IDやモデルが作ったIDで代用してはいけません。書込みが失敗・未実行・未検証なら、登録済みとしてrespondせず、不足や失敗を適切に示してください。書込みを伴うGoalにnot_requiredを使ってはいけません。MAGIによるResult IDの記載だけで完了するわけではなく、RITSUKOが保存済みResultと照合して確定します。
本人が資料・本人情報・最新情報など根拠を求めた場合、必要なSourceを読まずnot_requiredとしてはいけません。
読み取り・登録はoperation_catalogから登録済みの操作IDを選び、invokeとoperation_requestで指定してください。登録されていない操作を提案せず、実行済みとも扱わないでください。登録候補は本人の原文内の根拠範囲、既存Entity ID、明示の属性を使ってください。PKB登録のevidence_startとevidence_endはuser_input.rawのUnicode文字インデックスであり、先頭を0、終了位置を範囲に含まないPython式の半開区間とします（バイト数やトークン数ではありません）。指定したraw[evidence_start:evidence_end]には対象のentity_mentionと『機種は値』などの属性名・値の組を、末尾の値まで欠けずに含めてください。単一の明確な申告文なら全文を根拠範囲にできます。文字数を推測で補わず、原文を直接数えてください。model（機種/型番/モデル）とmanufacturer（メーカー/製造元）の属性登録は、本人原文の『機種は○○』『メーカーは○○』のように属性名と値が隣接して明示されたときだけ提案してください。単なる含意・曖昧な言い回しでは登録せず確認してください。PKB登録先のentity_idを推測・創作してはいけません。対象IDが未確認なら、まずpkb.searchで既存の対象・部品関係を確認し、観測のresolved_entityなどPKBが返した実在IDのみ利用してください。解決できなければask_user等で確認し、書込み完了と主張しないでください。利用不可のSourceを実行済みとせず、個人情報をWeb検索へ送信しないでください。
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
        "source": {"type": "string", "enum": list(AUTO_READ_SOURCES)},
        "query": {"type": "string"},
        "reason": {"type": "string"},
    },
}
OPERATION_REQUEST = {
    "type": "object", "additionalProperties": False,
    "required": ["operation_id", "arguments", "reason"],
    "properties": {
        "operation_id": {"type": "string", "enum": list(READ_OPERATIONS)},
        "arguments": {
            "type": "object", "additionalProperties": False,
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        "reason": {"type": "string"},
    },
}

WRITE_OPERATION_REQUEST = {
    "type": "object", "additionalProperties": False,
    "required": ["operation_id", "arguments", "reason"],
    "properties": {
        "operation_id": {"type": "string", "enum": [WRITE_OPERATION_ID]},
        "arguments": {
            "type": "object", "additionalProperties": False,
            "required": sorted(WRITE_ARGUMENT_KEYS),
            "properties": {
                "entity_id": {"type": "string"},
                "entity_mention": {"type": "string"},
                "predicate": {"type": "string", "enum": sorted(KNOWN_PREDICATES)},
                "value": {"type": "string"},
                "evidence_start": {"type": "integer"},
                "evidence_end": {"type": "integer"},
            },
        },
        "reason": {"type": "string"},
    },
}

GOAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["goal", "next", "fulfillment", "answer", "evidence",
                 "read_request", "operation_request", "question", "reason"],
    "properties": {
        "goal": {"type": "string"},
        "next": {"type": "string", "enum": [
            "respond", "invoke", "request_read", "ask_user", "wait", "unable",
        ]},
        "fulfillment": {"type": "string", "enum": [
            "met", "partial", "unmet", "unknown",
        ]},
        "answer": {"type": ["string", "null"]},
        "evidence": EVIDENCE,
        "read_request": {"anyOf": [READ_REQUEST, {"type": "null"}]},
        "operation_request": {"anyOf": [OPERATION_REQUEST, WRITE_OPERATION_REQUEST, {"type": "null"}]},
        "question": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
}


def validate_goal_turn(value: object) -> list[str]:
    """Do not trust a provider's schema enforcement or self-assessed success."""
    if not isinstance(value, dict):
        return ["goal:not_object"]
    errors: list[str] = []
    # Old persisted Goal turns do not have operation_request.
    legacy = "operation_request" not in value
    required = set(GOAL_SCHEMA["required"]) - ({"operation_request"} if legacy else set())
    if set(value) != required:
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
                or req.get("source") not in AUTO_READ_SOURCES
                or any(not isinstance(req.get(k), str) or not req[k].strip()
                       for k in ("query", "reason"))):
            errors.append("read_request:invalid")
    if (next_step == "request_read") != (req is not None):
        errors.append("read_request:state_mismatch")
    operation = value.get("operation_request")
    if operation is not None:
        if (not isinstance(operation, dict)
                or set(operation) != set(OPERATION_REQUEST["required"])
                or not isinstance(operation.get("arguments"), dict)
                or not isinstance(operation.get("reason"), str)
                or not operation["reason"].strip()):
            errors.append("operation_request:invalid")
        elif not isinstance(operation.get("operation_id"), str):
            errors.append("operation_request:invalid")
        elif operation.get("operation_id") in READ_OPERATIONS:
            if (set(operation["arguments"]) != {"query"}
                    or not isinstance(operation["arguments"]["query"], str)
                    or not operation["arguments"]["query"].strip()):
                errors.append("operation_request:invalid")
        elif operation.get("operation_id") == WRITE_OPERATION_ID:
            args = operation["arguments"]
            if (set(args) != WRITE_ARGUMENT_KEYS
                    or any(not isinstance(args.get(k), str) or not args[k].strip()
                           for k in ("entity_id", "entity_mention", "predicate", "value"))
                    or args.get("predicate") not in KNOWN_PREDICATES
                    or any(type(args.get(k)) is not int or args[k] < 0
                           for k in ("evidence_start", "evidence_end"))
                    or args["evidence_end"] <= args["evidence_start"]):
                errors.append("operation_request:invalid")
        else:
            errors.append("operation_request:invalid")
    if (next_step == "invoke") != (operation is not None):
        errors.append("operation_request:state_mismatch")
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
