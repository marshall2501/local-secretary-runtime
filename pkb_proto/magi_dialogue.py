"""Isolated classification-first RITSUKO/MAGI conversation experiment.

RITSUKO owns the task, selects the next MAGI question from the validated last
answer, and stops on missing observation, user clarification, invalid output or
a bounded turn limit. This module never reads PKB/Web, writes Tasks or performs
actions. The old full-contract Protocol v1 remains a separate comparison.
"""
from __future__ import annotations

from copy import deepcopy
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from .magi_client import OLLAMA
from .ritsuko_magi_protocol import default_resource_catalog

CATEGORIES = (
    "INFORMATION", "PROBLEM", "INVESTIGATION", "ACTION", "KNOWLEDGE",
    "PLANNING", "MONITORING", "CONVERSATION", "UNCLEAR",
)
STATES = (
    "READY", "NEED_INFORMATION", "NEED_CLARIFICATION",
    "KNOWLEDGE_CANDIDATE", "ACTION_PROPOSAL", "UNABLE",
)
SOURCES = (
    "current_context", "pkb", "task_history", "web", "files",
    "external_service", "pc_observation", "user",
)
MAX_TURNS = 4
PROMPT_VERSION = "d19-state-driven-v2"
QUESTION_PURPOSES = (
    "understand_or_disambiguate",
    "identify_missing_information",
    "evaluate_observation",
    "formulate_answer",
    "formulate_knowledge_candidate",
    "formulate_action",
    "review_or_repair",
)

SYSTEM = """あなたはPersonal Local Secretary AIのMAGI分析メンバーです。
ユーザー原文の意味を理解し、各回にRITSUKOから指定された「1つの問い」だけを分析してください。
RITSUKOがTask、次の問い、情報取得、権限、実行、終了を管理します。
MAGI自身はTool、PKB、Web、外部操作を実行しません。
情報を知らないことと依頼の意味が分からないことを混同しないでください。
PKB全件やWebの最新情報は提示されていません。根拠のない事実を補完しないでください。
JSON Schemaに厳密に従い、JSONだけを返してください。"""

CLASSIFY_QUESTION = """最初の仕事はユーザー原文の大まかな分類だけです。
ユーザーの実際の目的を1～2文で表し、主カテゴリと必要なら副カテゴリを選んでください。
PC、健康、家計等の話題ではなく「何を頼まれているか」で分類してください。
カテゴリ：
INFORMATION=既知の事実・特定情報を知りたい質問
PROBLEM=症状・不具合・問題の相談
INVESTIGATION=原因調査・比較・根拠付き分析
ACTION=操作・処理の実行依頼
KNOWLEDGE=本人の新情報や明示訂正の提示
PLANNING=方針・計画・選択肢の検討
MONITORING=継続監視・通知・リマインダー
CONVERSATION=会話・一般的な説明や相談
UNCLEAR=依頼の目的そのものが理解できない
情報や調査結果をまだ知らないだけならUNCLEARではありません。
複数の独立依頼があればmultiple_requests=trueとし、要約に記載してください。
例（固定キーワードで分類しない）：
「私の車の車種は？」→INFORMATION（本人データはまだ知らない）
「PCが頻繁に止まる」→PROBLEM
「今月と先月の支出を比較して」→INVESTIGATION
「購入したスマホは機種Aになった」→KNOWLEDGE
調査、回答、情報源の選択、実行案はこの回で生成せず分類結果だけを返してください。"""

DETAIL_RULES = """現在のEnvelopeにあるユーザー原文、分類、Task Context、Observationだけを根拠に分析してください。
初回分類は方向付けであり、後続の証拠と矛盾する場合は内容から考え直して構いません。
「情報源を使える」ことは「その情報を取得済み」ではありません。
取得すれば答えられそうという将来の見通しや検索手順は、ユーザーへの回答ではありません。
必要な情報があるならNEED_INFORMATIONとし、source / what / reasonを具体的に返してください。
request_id等の管理IDはRITSUKOが採番するので生成しないでください。
時間依存の公開情報で「最新・現在・本日」等の鮮度が要求される場合、鮮度を示すObservationが無ければPKBだけを最新情報の根拠にせず、web等のfresh external sourceを要求してください。
本人固有の既知情報・履歴・過去取得値はPKBやtask_historyを優先できますが、公開情報の現在性そのものとは区別してください。
source=userは通常のread sourceではありません。PKB / task_history / files / web等で解けないblocking情報にだけ使い、既存情報源で解決できる可能性があれば先にそちらを要求してください。
明確で低リスクな本人申告や訂正は、ユーザーが明示した最小事実だけでKNOWLEDGE_CANDIDATEにできます。理由・経緯・利用目的・製品仕様等の補足を記録候補化の必須条件にしないでください。
曖昧な参照語は、内部Observationから最も近い候補が一意で反証がない場合、その候補を「〜のことなら」のような限定表現付きで参照先として扱えます。候補が複数残る、または誤認の影響が大きい場合はNEED_CLARIFICATIONにしてください。過去履歴を無制限に広げ続けないでください。
追加Observationがある場合は必ず内容を検討し、前回と同じ情報要求を理由なく繰り返さないでください。
stateは現在の状況に最も近い1つを選択してください。
READY=現在与えられた根拠だけで、ユーザーが求めた答えそのものをanswer_candidateへ書ける
NEED_INFORMATION=情報源の読取・新しい観測が必要。information_requestsへ取得先と最小事実を返す
NEED_CLARIFICATION=情報源では解決できず本人に聞かなければ進められない
KNOWLEDGE_CANDIDATE=明確な新情報・訂正の最小事実をknowledge_candidateへ書ける
ACTION_PROPOSAL=権限・安全検査が必要な実行候補を提案
UNABLE=現状では対応できない
READY / KNOWLEDGE_CANDIDATE / ACTION_PROPOSALでも、成功・登録済み・完了・実行済みと宣言してはいけません。"""

PURPOSE_INSTRUCTIONS = {
    "understand_or_disambiguate": """依頼の意味・対象・要求結果を解決できるかを検討してください。
参照語や省略があっても、Task履歴・PKB等の利用可能な情報源で解決できそうなら、すぐ本人へ質問せずNEED_INFORMATIONでその情報を要求してください。
Observationから最も近い候補が一意で反証がなければ、限定表現付きでその候補を参照先として扱うことを検討してください。
候補が複数残る、または誤認がblockingならNEED_CLARIFICATIONにしてください。内部履歴を無制限に遡らないでください。""",
    "identify_missing_information": """依頼を進めるために、今の証拠だけで十分かを判断してください。
十分なら目的に応じてREADY / KNOWLEDGE_CANDIDATE / ACTION_PROPOSAL等を返してください。
不足なら必要最小限の事実と取得元をNEED_INFORMATIONで要求してください。
時間依存の公開情報はfreshnessを満たすweb等を優先し、source=userは他の利用可能な情報源では解けないblocking情報だけにしてください。""",
    "evaluate_observation": """新しく追加されたObservationを前回の不足情報と照合してください。
不足が解消したなら、現在の根拠から回答候補・記録候補・Action候補へ進んでください。
曖昧参照で一意の強い候補が得られたなら、限定表現付きで参照解決することを検討してください。
まだ不足する場合だけNEED_INFORMATIONを返し、前回より古い／広い履歴を漫然と掘り続けないでください。内部情報で解けないblocking曖昧さならNEED_CLARIFICATIONに切り替えてください。""",
    "formulate_answer": """現在の根拠だけでユーザーへ直接答えられるかを判断してください。
答えられるなら検索予定ではなく答えそのものをREADYのanswer_candidateへ返してください。
答えに未取得の事実が必要ならNEED_INFORMATIONへ切り替えてください。""",
    "formulate_knowledge_candidate": """ユーザー原文に明確な本人情報・訂正が含まれるかを判断してください。
低リスクで明確なら、ユーザーが実際に述べた最小事実だけをKNOWLEDGE_CANDIDATEのknowledge_candidateへ書いてください。
変更日・理由・利用目的・製品仕様等、ユーザーが述べていない補足は候補化の必須条件にしないでください。
重大な矛盾や対象不明など候補化を阻む情報だけが不足する場合に限りNEED_INFORMATION / NEED_CLARIFICATIONを選んでください。""",
    "formulate_action": """依頼された操作・監視・処理について、実行候補と前提・影響・必要権限を整理してください。
自分では実行せず、情報や対象が不足するなら先にNEED_INFORMATIONまたはNEED_CLARIFICATIONを選んでください。""",
    "review_or_repair": """直前のMAGI返答に矛盾または無進展の疑いがあります。現在のTask ContextとObservationから再点検してください。
検索・確認・実行の予定をREADYの回答とみなさず、Observation後に同一情報要求を無根拠に繰り返さないでください。
source=userを提案している場合は既存情報源で代替できないblocking情報だけを残してください。
曖昧参照で一意の強い候補があるなら限定付き解決、候補が残るなら本人確認を選び、内部履歴を際限なく広げないでください。
必要ならstateと要求内容を訂正し、進めない場合はその理由を具体化してください。""",
}

_CLASSIFICATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["category", "secondary_category", "understood_request", "reason",
                 "confidence", "multiple_requests", "clarification_question"],
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "secondary_category": {"type": ["string", "null"], "enum": list(CATEGORIES) + [None]},
        "understood_request": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "multiple_requests": {"type": "boolean"},
        "clarification_question": {"type": ["string", "null"]},
    },
}
_DETAIL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["understood_request", "state", "reason", "information_requests",
                 "question_for_user", "answer_candidate", "knowledge_candidate", "action_candidate"],
    "properties": {
        "understood_request": {"type": "string"},
        "state": {"type": "string", "enum": list(STATES)},
        "reason": {"type": "string"},
        "information_requests": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["source", "what", "reason"],
            "properties": {
                "source": {"type": "string", "enum": list(SOURCES)},
                "what": {"type": "string", "minLength": 1},
                "reason": {"type": "string"},
            },
        }},
        "question_for_user": {"type": ["string", "null"]},
        "answer_candidate": {"type": ["string", "null"]},
        "knowledge_candidate": {"type": ["string", "null"]},
        "action_candidate": {"type": ["string", "null"]},
    },
}

def validate_turn(stage: str, output: object) -> list[str]:
    """Independent validation; a model's API-side schema guarantee is not trusted."""
    schema = _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA
    if not isinstance(output, dict):
        return ["response:not_object"]
    errors = []
    expected = set(schema["required"])
    if set(output) != expected:
        errors.append("response:field_mismatch")
    for key, spec in schema["properties"].items():
        value = output.get(key)
        if key not in output:
            continue
        allowed_types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        actual_type = ("null" if value is None else "boolean" if isinstance(value, bool)
                       else "array" if isinstance(value, list) else "string" if isinstance(value, str)
                       else "object" if isinstance(value, dict) else "invalid")
        if actual_type not in allowed_types:
            errors.append(key + ":invalid_type")
        if "enum" in spec and value not in spec["enum"]:
            errors.append(key + ":invalid_enum")
    if stage == "classify":
        if output.get("category") != "UNCLEAR" and not str(output.get("understood_request") or "").strip():
            errors.append("understood_request:required")
        if output.get("category") == "UNCLEAR" and not str(output.get("clarification_question") or "").strip():
            errors.append("clarification_question:required")
    else:
        if not str(output.get("reason") or "").strip():
            errors.append("reason:required")
        items = output.get("information_requests")
        if isinstance(items, list):
            for i, entry in enumerate(items):
                if not isinstance(entry, dict) or set(entry) != {"source", "what", "reason"}:
                    errors.append(f"information_requests[{i}]:invalid_shape")
                elif (entry.get("source") not in SOURCES or
                      not isinstance(entry.get("what"), str) or not entry["what"].strip() or
                      not isinstance(entry.get("reason"), str)):
                    errors.append(f"information_requests[{i}]:invalid_values")
        if output.get("state") == "NEED_INFORMATION" and not items:
            errors.append("information_requests:required")
        if output.get("state") == "READY":
            if not isinstance(output.get("answer_candidate"), str) or not output["answer_candidate"].strip():
                errors.append("answer_candidate:required_for_ready")
            if items:
                errors.append("information_requests:unexpected_for_ready")
        if output.get("state") == "NEED_CLARIFICATION" and not str(output.get("question_for_user") or "").strip():
            errors.append("question_for_user:required")
        if output.get("state") == "KNOWLEDGE_CANDIDATE":
            if not isinstance(output.get("knowledge_candidate"), str) or not output["knowledge_candidate"].strip():
                errors.append("knowledge_candidate:required")
            if items:
                errors.append("information_requests:unexpected_for_knowledge_candidate")
    return errors

def call_guided_member(envelope: dict, *, model: str, timeout: float = 900.0) -> dict:
    """Only the transport; no semantic routing, DB/Web access or tool invocation."""
    stage = envelope["stage"]
    schema = _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA
    payload = {
        "model": model, "stream": False, "format": schema,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(envelope, ensure_ascii=False)},
        ],
        "options": {"temperature": 0, "num_predict": 1150},
    }
    try:
        req = Request(OLLAMA + "/api/chat",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            outer = json.load(resp)
        msg = outer.get("message") if isinstance(outer, dict) else None
        raw = msg.get("content") if isinstance(msg, dict) else None
        thinking = msg.get("thinking") if isinstance(msg, dict) else None
        diagnostic = {
            "content_length": len(raw) if isinstance(raw, str) else 0,
            "thinking_length": len(thinking) if isinstance(thinking, str) else 0,
            "done_reason": outer.get("done_reason"),
            "eval_count": outer.get("eval_count"),
        }
        if not isinstance(raw, str):
            return {"status": "invalid", "response": None, "errors": ["missing_content"], "diagnostic": diagnostic}
        try:
            data = json.loads(raw)
        except ValueError:
            return {"status": "invalid", "response": None, "errors": ["invalid_json"], "diagnostic": diagnostic}
        errors = validate_turn(stage, data)
        return {"status": "ok" if not errors else "invalid", "response": data,
                "errors": errors, "diagnostic": diagnostic}
    except (OSError, HTTPError, URLError, TimeoutError, ValueError) as exc:
        return {"status": "unavailable", "response": None,
                "errors": [type(exc).__name__],
                "diagnostic": {"error": type(exc).__name__}}

def _compose_question(session: dict, purpose: str, *, issue: str | None = None) -> str:
    if purpose not in QUESTION_PURPOSES:
        raise ValueError("unknown_question_purpose")
    classification = session.get("classification") or {}
    parts = [
        DETAIL_RULES,
        f"question_purpose={purpose}",
        PURPOSE_INSTRUCTIONS[purpose],
        "初回分類カテゴリ=" + str(classification.get("category") or "unknown"),
        "初回理解=" + str(classification.get("understood_request") or "未設定"),
    ]
    if issue:
        parts.append("RITSUKOが再点検を求める理由=" + issue)
    return "\n".join(parts)

def _select_question_purpose(session: dict) -> str:
    """Choose only the kind of judgment; MAGI still performs semantic analysis."""
    detail = session.get("detail") or {}
    if detail.get("state") == "NEED_INFORMATION" and session.get("observations"):
        category = (session.get("classification") or {}).get("category")
        if category == "UNCLEAR" and len(session["observations"]) >= 2:
            return "review_or_repair"
        return "evaluate_observation"
    if detail.get("state") == "READY":
        return "review_or_repair"
    category = (session.get("classification") or {}).get("category")
    if category == "UNCLEAR":
        return "understand_or_disambiguate"
    if category in {"ACTION", "MONITORING"}:
        return "formulate_action"
    if category == "KNOWLEDGE":
        return "formulate_knowledge_candidate"
    if category == "CONVERSATION":
        return "formulate_answer"
    return "identify_missing_information"

def _send(session: dict, stage: str, question_purpose: str, prompt: str, caller, *, timeout: float) -> dict | None:
    if len(session["turns"]) >= MAX_TURNS:
        session.update(status="stopped", next_step="max_turns_reached")
        return None
    envelope = {
        "protocol_variant": "state_driven_question_experiment",
        "prompt_version": session["prompt_version"],
        "task_id": session["task_id"], "turn": len(session["turns"]) + 1,
        "magi_member": "MELCHIOR", "stage": stage,
        "question_purpose": question_purpose,
        "question_from_ritsuko": prompt,
        "user_input": {"raw": session["user_raw"]},
        "resource_catalog": default_resource_catalog(),
        "task_context": {
            "classification": deepcopy(session.get("classification")),
            "previous_detail": deepcopy(session.get("detail")),
            "pending_information_requests": deepcopy(session.get("pending_requests") or []),
            "previous_turns": [
                {
                    "stage": turn["stage"],
                    "question_purpose": turn.get("question_purpose"),
                    "response": deepcopy(turn.get("response")),
                }
                for turn in session["turns"]
            ],
        },
        "observations": deepcopy(session["observations"]),
    }
    result = caller(envelope, model=session["model"], timeout=timeout)
    response = result.get("response")
    errors = list(result.get("errors") or [])
    if result.get("status") == "ok":
        errors += validate_turn(stage, response)
    status = "ok" if result.get("status") == "ok" and not errors else (
        "unavailable" if result.get("status") == "unavailable" else "invalid"
    )
    session["turns"].append({
        "stage": stage, "question_purpose": question_purpose,
        "request_envelope": envelope, "status": status,
        "response": deepcopy(response), "errors": errors,
        "diagnostic": deepcopy(result.get("diagnostic") or {}),
    })
    session["last_question_purpose"] = question_purpose
    if status != "ok":
        session.update(status="stopped", next_step="magi_" + status)
        return None
    return response

def _request_signatures(items: list[dict]) -> list[str]:
    return [
        json.dumps({"source": item["source"], "what": item["what"].strip()},
                   sort_keys=True, ensure_ascii=False)
        for item in items
    ]

def _apply_detail(session: dict, response: dict, caller, *, timeout: float, purpose: str) -> dict:
    session["detail"] = deepcopy(response)
    state = response["state"]

    if state == "READY" and not session["observations"] and purpose != "review_or_repair":
        prompt = _compose_question(
            session, "review_or_repair",
            issue="新しいObservationがないのにREADYとなったため、answer_candidateが実回答か作業予定かを再確認する",
        )
        reviewed = _send(session, "analyze", "review_or_repair", prompt, caller, timeout=timeout)
        if reviewed is None:
            return session
        return _apply_detail(session, reviewed, caller, timeout=timeout, purpose="review_or_repair")

    if state == "NEED_INFORMATION":
        requests = response["information_requests"]
        if (any(item.get("source") == "user" for item in requests)
                and purpose != "review_or_repair"
                and not session.get("user_source_reviewed")
                and len(session["turns"]) < MAX_TURNS):
            session["user_source_reviewed"] = True
            prompt = _compose_question(
                session, "review_or_repair",
                issue="source=userが提案された。既存のPKB / task_history / files / web等で代替できないblocking情報だけuser要求として残す",
            )
            reviewed = _send(session, "analyze", "review_or_repair", prompt, caller, timeout=timeout)
            if reviewed is None:
                return session
            return _apply_detail(session, reviewed, caller, timeout=timeout, purpose="review_or_repair")
        signatures = _request_signatures(requests)
        repeated = bool(session["observations"]) and bool(signatures) and all(
            signature in session["previous_request_signatures"] for signature in signatures
        )
        if repeated:
            if purpose != "review_or_repair" and len(session["turns"]) < MAX_TURNS:
                prompt = _compose_question(
                    session, "review_or_repair",
                    issue="Observation追加後も前回と同じ情報要求が返った。Observation不足の具体点を示すか、別の次手へ修正する",
                )
                reviewed = _send(session, "analyze", "review_or_repair", prompt, caller, timeout=timeout)
                if reviewed is None:
                    return session
                return _apply_detail(session, reviewed, caller, timeout=timeout, purpose="review_or_repair")
            session.update(status="stopped", next_step="repeated_request_without_progress")
            return session
        for signature in signatures:
            if signature not in session["previous_request_signatures"]:
                session["previous_request_signatures"].append(signature)
        session["pending_requests"] = [
            {"request_id": f"REQ-{session['task_id'][:8]}-{len(session['turns']):02d}-{i:02d}",
             **deepcopy(item)} for i, item in enumerate(requests, 1)
        ]
        if len(session["turns"]) >= MAX_TURNS:
            session.update(status="stopped", next_step="max_turns_reached_with_pending_information")
        elif requests and all(item.get("source") == "user" for item in requests):
            session.update(status="waiting_user", next_step="ask_user_for_information")
        else:
            session.update(status="waiting_information", next_step="review_information_requests")
    elif state == "NEED_CLARIFICATION":
        if len(session["turns"]) >= MAX_TURNS:
            session.update(status="stopped", next_step="max_turns_reached_with_user_question")
        else:
            session.update(status="waiting_user", next_step="consider_user_question")
    elif state in {"ACTION_PROPOSAL", "KNOWLEDGE_CANDIDATE"}:
        session.update(status="proposal_ready", next_step="review_proposal")
    elif state == "READY":
        session.update(status="candidate_ready", next_step="review_answer_candidate")
    else:
        session.update(status="stopped", next_step="unable")
    return session

def _advance(session: dict, caller, *, timeout: float) -> dict:
    purpose = _select_question_purpose(session)
    prompt = _compose_question(session, purpose)
    response = _send(session, "analyze", purpose, prompt, caller, timeout=timeout)
    if response is None:
        return session
    return _apply_detail(session, response, caller, timeout=timeout, purpose=purpose)

def start_dialogue(user_raw: str, *, model: str, timeout: float = 900.0,
                   caller=call_guided_member) -> dict:
    session = {
        "task_id": str(uuid4()), "user_raw": user_raw.strip(), "model": model,
        "prompt_version": PROMPT_VERSION,
        "status": "running", "next_step": "classify",
        "classification": None, "detail": None,
        "observations": [], "pending_requests": [], "previous_request_signatures": [],
        "user_source_reviewed": False, "last_question_purpose": None,
        "turns": [], "legacy_router_used": False, "tool_read_executed": False,
    }
    if not session["user_raw"] or not model:
        session.update(status="stopped", next_step="invalid_input")
        return session
    classification = _send(
        session, "classify", "classify", CLASSIFY_QUESTION, caller, timeout=timeout
    )
    if classification is None:
        return session
    session["classification"] = deepcopy(classification)
    return _advance(session, caller, timeout=timeout)

def continue_with_observation(session: dict, observation_text: str, *,
                              timeout: float = 900.0, caller=call_guided_member) -> dict:
    """Dev-only injection; not an actual PKB/Web read nor an authenticated source."""
    updated = deepcopy(session)
    if updated.get("status") != "waiting_information":
        raise ValueError("not_waiting_for_information")
    if not isinstance(observation_text, str) or not observation_text.strip():
        raise ValueError("empty_observation")
    if len(updated["turns"]) >= MAX_TURNS:
        updated.update(status="stopped", next_step="max_turns_reached")
        return updated
    updated["observations"].append({
        "source": "manual_test_input", "verified": False,
        "text": observation_text.strip()[:4000],
        "responds_to": [x["request_id"] for x in updated["pending_requests"]],
    })
    updated["pending_requests"] = []
    updated["status"] = "running"
    updated["next_step"] = "evaluate_observation"
    return _advance(updated, caller, timeout=timeout)

def export_dialogue(session: dict) -> dict:
    """No model Thinking text and no Ollama raw response bytes."""
    return deepcopy(session)
