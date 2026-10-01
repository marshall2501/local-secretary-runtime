"""Manual local Ollama extraction experiment, no database writes or gold answers.

Accepts ONLY the checked-in fictional corpus. Outputs candidates and errors
as an observational report, not a passing score or accepted memory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

from .ollama_runtime import configured_context_tokens

from .episode_intake import load_fixture
from .extraction_service import extraction_messages, inspect_model_output


def local_model(episode: dict, model: str, timeout: int) -> object:
    payload = {
        "model": model, "stream": False, "format": "json",
        "messages": extraction_messages(episode),
        "options": {"temperature": 0, "num_predict": 1100, "num_ctx": configured_context_tokens()},
    }
    req = Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        outer = json.load(resp)
    if not isinstance(outer, dict) or not isinstance(outer.get("message"), dict):
        raise ValueError("Unexpected local model response")
    raw = outer["message"].get("content")
    if not isinstance(raw, str):
        raise ValueError("Missing model response content")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if not args.model or any(c.isspace() for c in args.model):
        raise ValueError("Model must be one installed local Ollama model name")
    if not 5 <= args.timeout <= 600:
        raise ValueError("Invalid per-episode timeout")
    corpus = load_fixture(Path(__file__).parent / "fixtures" / "episodes.json")
    accepted, errors = 0, 0
    for episode in corpus:
        raw = local_model(episode, args.model, args.timeout)
        result = inspect_model_output(episode, raw)
        accepted += len(result.candidates)
        errors += len(result.errors)
        print(json.dumps({
            "episode": result.episode_id,
            "candidate_count": len(result.candidates),
            "rejected_count": len(result.errors),
            "candidates": [
                {"entity": c.entity_mention, "predicate": c.predicate,
                 "value": c.value, "label": c.label,
                 "disposition": c.disposition, "reason": c.reason,
                 "quote": c.quote}
                for c in result.candidates
            ],
            "errors": result.errors,
        }, ensure_ascii=False))
    print(f"COMPLETE: {len(corpus)} fictional episodes, {accepted} review-only candidates, "
          f"{errors} malformed or ungrounded proposals. NO DB WRITES. "
          "This is not a gold-answer correctness score.")


if __name__ == "__main__":
    main()
