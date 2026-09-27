"""Small, isolated local-LLM checks before testing PKB extraction.

No DB, gold answers or personal data. Fixed tasks isolate connectivity,
instruction reception, basic reasoning and extraction-prompt complexity.
"""
from __future__ import annotations

from .extraction_service import extraction_messages

MODES = (
    "疎通：固定文字列",
    "理解：原文復唱",
    "推論：簡単な計算",
    "抽出：簡略",
    "抽出：現行",
)
EXTRACTION_MODES = frozenset(("抽出：簡略", "抽出：現行"))


def request_for(episode: dict, mode: str) -> tuple[list[dict[str, str]], bool]:
    """Return messages and whether constrained JSON is required."""
    if mode == "疎通：固定文字列":
        return [
            {"role": "system", "content": "短い日本語の指示に従ってください。"},
            {"role": "user", "content": "次の文字列のみ返してください。説明不要：ABC123"},
        ], False
    if mode == "理解：原文復唱":
        return [
            {"role": "system", "content": "次の原文を一字一句変更せずに復唱してください。説明不要。"},
            {"role": "user", "content": episode["text"]},
        ], False
    if mode == "推論：簡単な計算":
        return [
            {"role": "system", "content": "簡単な計算に答えてください。答えは数字1つのみ。"},
            {"role": "user", "content": "架空の箱にリンゴが2個あります。さらに3個入れました。合計は何個ですか？"},
        ], False
    if mode == "抽出：簡略":
        return [
            {"role": "system", "content": (
                "与えられた原文から、明記されている対象名と出来事だけをJSONで返してください。"
                "形式は{\"entity\": \"...\", \"event\": \"...\"}。"
                "本文にない情報を加えず、JSON以外の文章は返さない。"
            )},
            {"role": "user", "content": episode["text"]},
        ], True
    if mode == "抽出：現行":
        return extraction_messages(episode), True
    raise ValueError("Unknown diagnostic mode")


EPISODE_UNUSED = frozenset(("疎通：固定文字列", "推論：簡単な計算"))


def build_ollama_payload(episode: dict, model: str, predict: int,
                         think: str, mode: str) -> dict:
    """Return the actual request body shared by GUI preview and HTTP sender."""
    messages, json_format = request_for(episode, mode)
    result = {
        "model": model,
        "stream": False,
        "messages": messages,
        "options": {"temperature": 0, "num_predict": predict},
    }
    if json_format:
        result["format"] = "json"
    if think == "無効":
        result["think"] = False
    return result


def judge_response(mode: str, episode: dict, content: str) -> str:
    """Narrow observable checks, NOT general semantic correctness."""
    if not content.strip():
        return "回答本文なし"
    if mode == "疎通：固定文字列":
        return "固定文字列一致" if content.strip() == "ABC123" else "固定文字列不一致"
    if mode == "理解：原文復唱":
        return "原文完全一致" if content.strip() == episode["text"] else "原文不一致"
    if mode == "推論：簡単な計算":
        return "正答（5）" if content.strip() == "5" else "回答が5以外"
    if mode in EXTRACTION_MODES:
        import json
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return "JSON不正"
        if mode == "抽出：簡略":
            if not isinstance(parsed, dict) or set(parsed) != {"entity", "event"}:
                return "簡略JSONスキーマ不一致"
            if not all(isinstance(parsed[k], str) and parsed[k].strip() for k in parsed):
                return "簡略JSONフィールド不正"
            return "簡略JSON構造OK・意味未検証"
        return "JSON構文OK・原文／意味は別検査"
    raise ValueError("Unknown diagnostic mode")
