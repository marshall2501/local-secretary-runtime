"""One-episode local Ollama JSON diagnostic. No database writes, no gold set.

Report only response metadata and small content fragments from fictional input.
Never print model thinking or disclose credentials.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from .ollama_runtime import configured_context_tokens

from .episode_intake import load_fixture
from .extraction_service import extraction_messages


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="qwen3.5:9b")
    args = p.parse_args()
    if not args.model or any(x.isspace() for x in args.model):
        raise ValueError("Invalid installed local model")
    episodes = load_fixture(Path(__file__).parent / "fixtures" / "episodes.json")
    episode = next(x for x in episodes if x["id"] == "pc-01")
    payload = {
        "model": args.model,
        "stream": False,
        "format": "json",
        "messages": extraction_messages(episode),
        "options": {"temperature": 0, "num_predict": 1100, "num_ctx": configured_context_tokens()},
    }
    req = Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=180) as resp:
        result = json.load(resp)
    message = result.get("message") or {}
    raw = message.get("content")
    if not isinstance(raw, str):
        raw = ""
    try:
        decoded = json.loads(raw)
        parse_result = "valid_json"
        parsed_shape = type(decoded).__name__
    except json.JSONDecodeError as exc:
        parse_result = f"invalid_json:{exc.msg}"
        parsed_shape = None
    print(json.dumps({
        "episode": episode["id"],
        "model": result.get("model"),
        "done": result.get("done"),
        "done_reason": result.get("done_reason"),
        "prompt_eval_count": result.get("prompt_eval_count"),
        "eval_count": result.get("eval_count"),
        "content_length": len(raw),
        "content_empty": not bool(raw.strip()),
        "content_begins_think_tag": raw.lstrip().startswith("<think>"),
        "content_begins_json_object": raw.lstrip().startswith("{"),
        "content_ends_json_object": raw.rstrip().endswith("}"),
        "thinking_field_present": "thinking" in message,
        "thinking_length": len(message.get("thinking") or ""),
        "json_parse": parse_result,
        "parsed_shape": parsed_shape,
        "content_first_100": raw[:100] if not raw.lstrip().startswith("<think>") else "[hidden: think tag]",
        "content_last_100": raw[-100:] if not raw.lstrip().startswith("<think>") else "[hidden: think tag]",
        "note": "One fictional episode only. No database access or model-setting change.",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
