"""Isolated classification-first RITSUKO/MAGI conversation experiment.

RITSUKO owns the task, selects the next MAGI question from the validated last
answer, and stops on missing observation, user clarification, invalid output or
a bounded turn limit. This module never reads PKB/Web, writes Tasks or performs
actions. The old full-contract Protocol v1 remains a separate comparison.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from .magi_client import OLLAMA
from .magi_settings import (
    DEFAULT_TIMEOUT_SECONDS, MEMBER_NAMES, PROVIDERS, fallback_member_specs,
)
from .ollama_runtime import DEFAULT_OLLAMA_CONTEXT_TOKENS, normalize_context_tokens
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
PROMPT_VERSION = "d19-state-driven-v4"
QUESTION_PURPOSES = (
    "understand_or_disambiguate",
    "identify_missing_information",
    "evaluate_observation",
    "formulate_answer",
    "formulate_knowledge_candidate",
    "formulate_action",
    "review_or_repair",
)

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
GEMINI_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_MEMBER_PRIORITY = {"MELCHIOR": 0, "CASPER": 1, "BALTHASAR": 2}

PREREQUISITE_KNOWLEDGE = """前提知識：
このシステムは、現在の依頼と取得済み情報で判断し、必要な事実が不足する場合は情報を取得し、その結果をObservationとして後続の判断へ渡しながら処理を進めます。
Resource Catalogは今回利用可能な情報源・能力とアクセス条件を示します。利用可能であることは、その内容を取得済みという意味ではありません。
Observationは、今回までに実際に取得・受理され、判断材料として提示された事実・結果です。
PKBはユーザー本人について蓄積されたPersonal Knowledge Base、Task Historyは過去・進行中Taskの状態や結果、Webは公開情報、Filesは利用可能な文書・ファイルを確認する情報源です。
Userは必要事項を本人へ確認する場合の情報源です。
「利用可能」「要求済み」「取得済み」「検証済み」「実行済み」は別の状態です。"""

COMMON_INSTRUCTIONS = """共通指示：
現在与えられている入力を使い、今回指定された判断だけを行ってください。
今回要求された判断の範囲を超えて、別の目的へ処理を広げないでください。
取得済み情報と未取得情報を区別し、与えられていない事実を既知の事実として推測で補完しないでください。
指定されたJSON Schema、許可値、必須項目に厳密に従い、JSONだけを返してください。"""

SYSTEM = PREREQUISITE_KNOWLEDGE + "\n\n" + COMMON_INSTRUCTIONS

CLASSIFY_QUESTION = """今回の目的は、ユーザーが最終的に何を求めているかを大まかに理解・分類し、後続判断の入口を作ることです。
話題の分野ではなく、ユーザーが求めている結果を基準に主カテゴリを1つ選んでください。
understood_requestには、ユーザーが最終的に求めている結果が分かる短い理解を書いてください。

カテゴリ判断基準：
INFORMATION=既存の事実・値・状態・公開情報などを知ることが主目的
PROBLEM=困りごと・異常・症状などを解決・改善することが主目的
INVESTIGATION=原因・理由・関係・差異などを調査・分析することが主目的
ACTION=何かを実際に実行・変更することが主目的
KNOWLEDGE=ユーザー自身について新しい事実・訂正等をシステムへ伝えることが主目的
PLANNING=今後の方法・順序・方針・選択肢などを計画することが主目的
MONITORING=将来も継続して確認し、変化や条件成立を追跡することが主目的
CONVERSATION=上記の具体的Taskには該当せず、会話・一般的説明・意見交換等が主目的
UNCLEAR=情報不足ではなく、ユーザーが何を求めているのか自体を十分特定できない

境界例（固定キーワードで分類しない）：
「GPUの最新ドライバーは？」→INFORMATION
「なぜゲーム中に固まる？」→INVESTIGATION
「ゲーム中に固まって困っている。直したい」→PROBLEM
「ドライバーを更新して」→ACTION
「スマホをPixel 10に替えた」→KNOWLEDGE

答えるための情報や調査結果をまだ知らないだけならUNCLEARにしないでください。
独立した依頼が複数含まれる場合だけmultiple_requests=trueにしてください。この回では依頼の分割や実行は行いません。
この段階では、情報源の選択、不足情報の詳細分析、調査手順、回答生成、Action生成、記憶候補生成を行わないでください。
reasonは分類理由を短く返し、confidenceは理解・分類の確信度をhigh / medium / lowで返してください。"""

DETAIL_RULES = """初回分類は方向付けであり、後続の証拠と矛盾する場合は内容から考え直して構いません。
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
    "required": ["category", "understood_request", "reason", "confidence", "multiple_requests"],
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "understood_request": {"type": "string", "minLength": 1},
        "reason": {"type": "string", "minLength": 1},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "multiple_requests": {"type": "boolean"},
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
        if not str(output.get("understood_request") or "").strip():
            errors.append("understood_request:required")
        if not str(output.get("reason") or "").strip():
            errors.append("reason:required")
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

def _call_ollama_guided(
    envelope: dict, *, model: str, timeout: float, base_url: str | None = None,
    context_window_tokens: int | None = None,
) -> dict:
    """Ollama transport only; no semantic routing or tool access."""
    stage = envelope["stage"]
    schema = _CLASSIFICATION_SCHEMA if stage == "classify" else _DETAIL_SCHEMA
    payload = {
        "model": model, "stream": False, "format": schema,
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
        req = Request(
            endpoint + "/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            outer = json.load(resp)
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
            return {"status": "invalid", "response": None,
                    "errors": ["missing_content"], "diagnostic": diagnostic}
        try:
            data = json.loads(raw)
        except ValueError:
            return {"status": "invalid", "response": None,
                    "errors": ["invalid_json"], "diagnostic": diagnostic}
        errors = validate_turn(stage, data)
        return {"status": "ok" if not errors else "invalid", "response": data,
                "errors": errors, "diagnostic": diagnostic}
    except (OSError, HTTPError, URLError, TimeoutError, ValueError) as exc:
        return {"status": "unavailable", "response": None,
                "errors": [type(exc).__name__],
                "diagnostic": {"provider": "ollama", "error": type(exc).__name__}}


def call_guided_member(
    envelope: dict, *, model: str, timeout: float = DEFAULT_TIMEOUT_SECONDS,
    context_window_tokens: int = DEFAULT_OLLAMA_CONTEXT_TOKENS,
) -> dict:
    """Backward-compatible local Ollama entry point used by isolated tests."""
    return _call_ollama_guided(
        envelope, model=model, timeout=timeout, base_url=OLLAMA,
        context_window_tokens=context_window_tokens,
    )


def panel_member_specs(local_model: str) -> list[dict]:
    """Backward-compatible name for env bootstrap/fallback member settings."""
    return fallback_member_specs(local_model)


def _extract_openai_output_text(outer: object) -> str | None:
    if not isinstance(outer, dict):
        return None
    parts = []
    for item in outer.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                value = content.get("text")
                if isinstance(value, str):
                    parts.append(value)
    return "".join(parts) if parts else None


def _call_openai_guided(
    envelope: dict,
    *,
    model: str,
    timeout: float,
    base_url: str | None = None,
    credential_env: str | None = None,
) -> dict:
    """OpenAI Responses adapter; secret values are never returned."""
    credential_name = credential_env or "OPENAI_API_KEY"
    api_key = os.environ.get(credential_name, "").strip()
    if not api_key:
        return {
            "status": "unavailable", "response": None,
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
        req = Request(
            endpoint + "/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
            },
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            outer = json.load(resp)
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
            return {"status": "invalid", "response": None,
                    "errors": ["missing_content"], "diagnostic": diagnostic}
        try:
            data = json.loads(raw)
        except ValueError:
            return {"status": "invalid", "response": None,
                    "errors": ["invalid_json"], "diagnostic": diagnostic}
        errors = validate_turn(stage, data)
        return {"status": "ok" if not errors else "invalid", "response": data,
                "errors": errors, "diagnostic": diagnostic}
    except HTTPError as exc:
        return {"status": "unavailable", "response": None,
                "errors": ["HTTPError"],
                "diagnostic": {"provider": "openai", "http_status": exc.code}}
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "response": None,
                "errors": [type(exc).__name__],
                "diagnostic": {"provider": "openai", "error": type(exc).__name__}}


def _extract_gemini_text(outer: object) -> str | None:
    if not isinstance(outer, dict):
        return None
    for candidate in outer.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts") or []
        text_parts = [
            part.get("text") for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        if text_parts:
            return "".join(text_parts)
    return None


def _call_gemini_guided(
    envelope: dict,
    *,
    model: str,
    timeout: float,
    base_url: str | None = None,
    credential_env: str | None = None,
) -> dict:
    """Gemini generateContent adapter using structured JSON output."""
    credential_name = credential_env or "GEMINI_API_KEY"
    api_key = os.environ.get(credential_name, "").strip()
    if not api_key:
        return {
            "status": "unavailable", "response": None,
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
            "temperature": 0,
            "maxOutputTokens": 1600,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    try:
        req = Request(
            endpoint + "/models/" + quote(model, safe="") + ":generateContent",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            outer = json.load(resp)
        raw = _extract_gemini_text(outer)
        usage = outer.get("usageMetadata") if isinstance(outer, dict) else None
        diagnostic = {
            "provider": "gemini",
            "prompt_tokens": usage.get("promptTokenCount") if isinstance(usage, dict) else None,
            "candidate_tokens": usage.get("candidatesTokenCount") if isinstance(usage, dict) else None,
            "total_tokens": usage.get("totalTokenCount") if isinstance(usage, dict) else None,
        }
        if not isinstance(raw, str):
            return {"status": "invalid", "response": None,
                    "errors": ["missing_content"], "diagnostic": diagnostic}
        try:
            data = json.loads(raw)
        except ValueError:
            return {"status": "invalid", "response": None,
                    "errors": ["invalid_json"], "diagnostic": diagnostic}
        errors = validate_turn(stage, data)
        return {"status": "ok" if not errors else "invalid", "response": data,
                "errors": errors, "diagnostic": diagnostic}
    except HTTPError as exc:
        return {"status": "unavailable", "response": None,
                "errors": ["HTTPError"],
                "diagnostic": {"provider": "gemini", "http_status": exc.code}}
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "response": None,
                "errors": [type(exc).__name__],
                "diagnostic": {"provider": "gemini", "error": type(exc).__name__}}


def _normalized_member_specs(
    member_specs: list[dict] | None, local_model: str
) -> list[dict]:
    source = member_specs if member_specs is not None else panel_member_specs(local_model)
    normalized = []
    seen = set()
    for raw in source:
        name = str(raw.get("name") or "").strip().upper()
        provider = str(raw.get("provider") or "").strip().lower()
        model = str(raw.get("model") or "").strip()
        if name not in MEMBER_NAMES or name in seen:
            raise ValueError("invalid_magi_member_specs")
        if provider not in PROVIDERS:
            raise ValueError("unsupported_provider")
        seen.add(name)
        item = {
            "name": name,
            "profile_id": raw.get("profile_id"),
            "profile_label": raw.get("profile_label") or f"{provider} / {model or '-'}",
            "provider": provider,
            "model": model,
            "endpoint": str(raw.get("endpoint") or "").strip() or None,
            "credential_env": (
                str(raw.get("credential_env") or "").strip() or None
            ),
            "context_window_tokens": (
                normalize_context_tokens(raw.get("context_window_tokens"))
                if provider == "ollama"
                else None
            ),
            "weight": float(raw.get("weight") or 1.0),
            "timeout_seconds": int(raw.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
            "enabled": bool(raw.get("enabled") and model),
            "settings_source": raw.get("settings_source") or "explicit",
        }
        if item["weight"] <= 0 or not 1 <= item["timeout_seconds"] <= 3600:
            raise ValueError("invalid_magi_member_specs")
        normalized.append(item)
    if not any(item["enabled"] for item in normalized):
        raise ValueError("no_enabled_magi_member")
    return normalized


def _decision_signature(stage: str, response: dict) -> str:
    if stage == "classify":
        return (
            "category=" + str(response.get("category"))
            + ";multiple_requests=" + str(bool(response.get("multiple_requests"))).lower()
        )
    state = str(response.get("state"))
    if state == "NEED_INFORMATION":
        sources = sorted({
            str(item.get("source"))
            for item in (response.get("information_requests") or [])
            if isinstance(item, dict) and item.get("source")
        })
        return "state=NEED_INFORMATION;sources=" + ",".join(sources)
    return "state=" + state


def select_weighted_consensus(stage: str, member_results: list[dict]) -> dict:
    """Choose a structured MAGI result by weighted vote over validated decisions."""
    valid = [
        item for item in member_results
        if item.get("status") == "ok"
        and isinstance(item.get("response"), dict)
        and float(item.get("weight") or 0) > 0
    ]
    if not valid:
        return {
            "status": "unavailable", "response": None,
            "reason": "no_valid_member", "votes": {},
            "selected_member": None, "decision_signature": None,
        }

    votes: dict[str, float] = {}
    for item in valid:
        signature = _decision_signature(stage, item["response"])
        votes[signature] = votes.get(signature, 0.0) + float(item["weight"])

    best_score = max(votes.values())
    winners = [
        signature for signature, score in votes.items()
        if abs(score - best_score) < 1e-9
    ]
    if len(winners) != 1:
        return {
            "status": "disagreement", "response": None,
            "reason": "weighted_vote_tie", "votes": votes,
            "selected_member": None, "decision_signature": None,
        }

    winner = winners[0]
    agreeing = [
        item for item in valid
        if _decision_signature(stage, item["response"]) == winner
    ]
    representative = max(
        agreeing,
        key=lambda item: (
            float(item["weight"]),
            -_MEMBER_PRIORITY.get(str(item.get("name")), 99),
        ),
    )
    return {
        "status": "ok",
        "response": deepcopy(representative["response"]),
        "reason": "weighted_vote",
        "votes": votes,
        "selected_member": representative.get("name"),
        "decision_signature": winner,
        "valid_members": [item.get("name") for item in valid],
    }


def _call_panel_member(spec: dict, envelope: dict, *, timeout: float) -> dict:
    member_envelope = deepcopy(envelope)
    member_envelope["magi_member"] = spec["name"]
    member_timeout = float(spec.get("timeout_seconds") or timeout)
    provider = spec["provider"]
    started = time.perf_counter()
    if provider == "ollama":
        result = _call_ollama_guided(
            member_envelope,
            model=spec["model"],
            timeout=member_timeout,
            base_url=spec.get("endpoint"),
            context_window_tokens=spec.get("context_window_tokens"),
        )
    elif provider == "openai":
        result = _call_openai_guided(
            member_envelope,
            model=spec["model"],
            timeout=member_timeout,
            base_url=spec.get("endpoint"),
            credential_env=spec.get("credential_env"),
        )
    elif provider == "gemini":
        result = _call_gemini_guided(
            member_envelope,
            model=spec["model"],
            timeout=member_timeout,
            base_url=spec.get("endpoint"),
            credential_env=spec.get("credential_env"),
        )
    else:
        result = {
            "status": "unavailable", "response": None,
            "errors": ["unsupported_provider"], "diagnostic": {},
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


def call_guided_panel(
    envelope: dict,
    *,
    model: str = "",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    member_specs: list[dict] | None = None,
) -> dict:
    """Run enabled provider-independent MAGI slots concurrently."""
    specs = [
        spec for spec in _normalized_member_specs(member_specs, model)
        if spec["enabled"]
    ]
    member_results: list[dict | None] = [None] * len(specs)
    with ThreadPoolExecutor(max_workers=max(1, len(specs))) as executor:
        future_to_index = {
            executor.submit(_call_panel_member, spec, envelope, timeout=timeout): index
            for index, spec in enumerate(specs)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                member_results[index] = future.result()
            except Exception as exc:  # isolate one provider/member failure
                spec = specs[index]
                member_results[index] = {
                    "name": spec["name"],
                    "profile_id": spec.get("profile_id"),
                    "provider": spec["provider"],
                    "model": spec["model"],
                    "weight": spec["weight"],
                    "timeout_seconds": spec.get("timeout_seconds"),
                    "context_window_tokens": spec.get("context_window_tokens"),
                    "status": "unavailable", "response": None,
                    "errors": [type(exc).__name__],
                    "diagnostic": {"error": type(exc).__name__},
                }

    completed = [item for item in member_results if isinstance(item, dict)]
    consensus = select_weighted_consensus(envelope["stage"], completed)
    status = consensus["status"]
    return {
        "status": status,
        "response": deepcopy(consensus.get("response")),
        "errors": [] if status == "ok" else [str(consensus.get("reason") or status)],
        "diagnostic": {
            "mode": "weighted_panel",
            "enabled_members": [spec["name"] for spec in specs],
            "assignments": [
                {
                    "name": spec["name"],
                    "provider": spec["provider"],
                    "model": spec["model"],
                    "weight": spec["weight"],
                    "timeout_seconds": spec["timeout_seconds"],
                }
                for spec in specs
            ],
            "valid_members": list(consensus.get("valid_members") or []),
        },
        "member_results": completed,
        "consensus": consensus,
    }


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

def _stop_between_turns(session: dict, stop_requested=None) -> bool:
    """Honor a user stop request only at a safe boundary between LLM turns."""
    if stop_requested is None or not stop_requested():
        return False
    session.update(status="stopped", next_step="user_requested_stop")
    return True

def _send(session: dict, stage: str, question_purpose: str, prompt: str, caller, *, timeout: float, stop_requested=None, on_turn_start=None) -> dict | None:
    if len(session["turns"]) >= MAX_TURNS:
        session.update(status="stopped", next_step="max_turns_reached")
        return None
    if _stop_between_turns(session, stop_requested):
        return None
    turn_number = len(session["turns"]) + 1
    envelope = {
        "protocol_variant": "state_driven_question_experiment",
        "prompt_version": session["prompt_version"],
        "task_id": session["task_id"], "turn": turn_number,
        "magi_member": "MAGI_PANEL", "stage": stage,
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
    if caller is call_guided_panel:
        result = caller(
            envelope,
            model=session.get("model") or "",
            timeout=timeout,
            member_specs=session.get("member_specs"),
        )
    else:
        result = caller(envelope, model=session.get("model") or "", timeout=timeout)
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
        "stage": stage, "question_purpose": question_purpose,
        "request_envelope": envelope, "status": status,
        "response": deepcopy(response), "errors": errors,
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
        session.update(status="waiting_user", next_step="magi_disagreement_requires_clarification")
        return None
    if status != "ok":
        session.update(status="stopped", next_step="magi_" + status)
        return None
    session["magi_disagreement"] = None
    return response

def _request_signatures(items: list[dict]) -> list[str]:
    return [
        json.dumps({"source": item["source"], "what": item["what"].strip()},
                   sort_keys=True, ensure_ascii=False)
        for item in items
    ]

def _apply_detail(session: dict, response: dict, caller, *, timeout: float, purpose: str, stop_requested=None, on_turn_start=None) -> dict:
    session["detail"] = deepcopy(response)
    session["user_question"] = None
    state = response["state"]
    if _stop_between_turns(session, stop_requested):
        return session

    if state == "READY" and not session["observations"] and purpose != "review_or_repair":
        prompt = _compose_question(
            session, "review_or_repair",
            issue="新しいObservationがないのにREADYとなったため、answer_candidateが実回答か作業予定かを再確認する",
        )
        reviewed = _send(session, "analyze", "review_or_repair", prompt, caller, timeout=timeout, stop_requested=stop_requested, on_turn_start=on_turn_start)
        if reviewed is None:
            return session
        return _apply_detail(session, reviewed, caller, timeout=timeout, purpose="review_or_repair", stop_requested=stop_requested, on_turn_start=on_turn_start)

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
            reviewed = _send(session, "analyze", "review_or_repair", prompt, caller, timeout=timeout, stop_requested=stop_requested, on_turn_start=on_turn_start)
            if reviewed is None:
                return session
            return _apply_detail(session, reviewed, caller, timeout=timeout, purpose="review_or_repair", stop_requested=stop_requested, on_turn_start=on_turn_start)
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
                reviewed = _send(session, "analyze", "review_or_repair", prompt, caller, timeout=timeout, stop_requested=stop_requested, on_turn_start=on_turn_start)
                if reviewed is None:
                    return session
                return _apply_detail(session, reviewed, caller, timeout=timeout, purpose="review_or_repair", stop_requested=stop_requested, on_turn_start=on_turn_start)
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
            session["user_question"] = "確認したいこと: " + " / ".join(
                item["what"] for item in requests
            )
            session.update(status="waiting_user", next_step="ask_user_for_information")
        else:
            session.update(status="waiting_information", next_step="review_information_requests")
    elif state == "NEED_CLARIFICATION":
        if len(session["turns"]) >= MAX_TURNS:
            session.update(status="stopped", next_step="max_turns_reached_with_user_question")
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

def _advance(session: dict, caller, *, timeout: float, stop_requested=None, on_turn_start=None) -> dict:
    if _stop_between_turns(session, stop_requested):
        return session
    purpose = _select_question_purpose(session)
    prompt = _compose_question(session, purpose)
    response = _send(session, "analyze", purpose, prompt, caller, timeout=timeout, stop_requested=stop_requested, on_turn_start=on_turn_start)
    if response is None:
        return session
    return _apply_detail(session, response, caller, timeout=timeout, purpose=purpose, stop_requested=stop_requested, on_turn_start=on_turn_start)

def start_dialogue(
    user_raw: str,
    *,
    model: str = "",
    member_specs: list[dict] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel,
    stop_requested=None,
    on_turn_start=None,
) -> dict:
    specs = _normalized_member_specs(member_specs, model) if caller is call_guided_panel else (
        deepcopy(member_specs) if member_specs is not None else []
    )
    session = {
        "task_id": str(uuid4()), "user_raw": user_raw.strip(), "model": model,
        "prompt_version": PROMPT_VERSION, "magi_mode": "weighted_panel",
        "member_specs": specs,
        "status": "running", "next_step": "classify",
        "classification": None, "detail": None,
        "observations": [], "pending_requests": [], "previous_request_signatures": [],
        "conversation_context": [], "user_question": None, "magi_disagreement": None,
        "user_source_reviewed": False, "last_question_purpose": None,
        "turns": [], "legacy_router_used": False, "tool_read_executed": False,
    }
    if not session["user_raw"]:
        session.update(status="stopped", next_step="invalid_input")
        return session
    if caller is call_guided_panel and not specs:
        session.update(status="stopped", next_step="no_magi_member")
        return session
    classification = _send(
        session, "classify", "classify", CLASSIFY_QUESTION, caller, timeout=timeout,
        stop_requested=stop_requested, on_turn_start=on_turn_start,
    )
    if classification is None:
        return session
    session["classification"] = deepcopy(classification)
    if classification["multiple_requests"]:
        session.update(status="stopped", next_step="multiple_requests_detected")
        return session
    return _advance(session, caller, timeout=timeout, stop_requested=stop_requested, on_turn_start=on_turn_start)

def continue_with_observation(session: dict, observation_text: str, *,
                              timeout: float = DEFAULT_TIMEOUT_SECONDS, caller=call_guided_panel,
                              stop_requested=None, on_turn_start=None) -> dict:
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
    return _advance(updated, caller, timeout=timeout, stop_requested=stop_requested, on_turn_start=on_turn_start)

def continue_with_user_clarification(session: dict, user_text: str, *,
                                     timeout: float = DEFAULT_TIMEOUT_SECONDS,
                                     caller=call_guided_panel,
                                     stop_requested=None, on_turn_start=None) -> dict:
    """Continue a waiting_user session without exposing internal pattern names."""
    updated = deepcopy(session)
    if updated.get("status") != "waiting_user":
        raise ValueError("not_waiting_for_user")
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("empty_user_clarification")

    text = user_text.strip()[:4000]
    updated.setdefault("conversation_context", []).append({
        "role": "user", "text": text,
    })
    updated["conversation_context"] = updated["conversation_context"][-4:]
    updated["user_question"] = None
    updated["magi_disagreement"] = None
    updated["status"] = "running"

    if updated.get("classification") is None:
        updated["next_step"] = "classify_with_context"
        classification = _send(
            updated, "classify", "classify", CLASSIFY_QUESTION,
            caller, timeout=timeout, stop_requested=stop_requested,
            on_turn_start=on_turn_start,
        )
        if classification is None:
            return updated
        updated["classification"] = deepcopy(classification)
        if classification["multiple_requests"]:
            updated.update(status="stopped", next_step="multiple_requests_detected")
            return updated
        return _advance(updated, caller, timeout=timeout, stop_requested=stop_requested, on_turn_start=on_turn_start)

    responds_to = [
        item["request_id"] for item in updated.get("pending_requests") or []
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
    response = _send(
        updated, "analyze", "evaluate_observation", prompt,
        caller, timeout=timeout, stop_requested=stop_requested,
        on_turn_start=on_turn_start,
    )
    if response is None:
        return updated
    return _apply_detail(
        updated, response, caller, timeout=timeout,
        purpose="evaluate_observation", stop_requested=stop_requested,
        on_turn_start=on_turn_start,
    )


def export_dialogue(session: dict) -> dict:
    """No model Thinking text and no Ollama raw response bytes."""
    return deepcopy(session)
