"""Pure presentation helpers for the fictional-only desktop extraction workbench."""
from __future__ import annotations

import json
from typing import Any


def analyze_reply(episode: dict, response: dict[str, Any], extraction,
                  *, expect_json: bool = True) -> dict[str, Any]:
    """Keep metadata and harmless fictional snippets; never export model thinking."""
    message = response.get("message") or {}
    raw = message.get("content")
    raw = raw if isinstance(raw, str) else ""
    try:
        parsed = json.loads(raw)
        json_state = "valid_json"
        json_type = type(parsed).__name__
    except (ValueError, TypeError) as exc:
        json_state = "invalid_json: " + str(exc)[:160]
        json_type = None
    if not expect_json:
        json_state, json_type = "not_requested", None
    embedded_thought = raw.lstrip().startswith("<think>")
    return {
        "episode": episode["id"],
        "model": response.get("model"),
        "done_reason": response.get("done_reason"),
        "prompt_eval_count": response.get("prompt_eval_count"),
        "eval_count": response.get("eval_count"),
        "content_length": len(raw),
        "thinking_field_present": "thinking" in message,
        "thinking_length": len(message.get("thinking") or ""),
        "content_begins_think_tag": embedded_thought,
        "json_parse": json_state,
        "json_type": json_type,
        "candidate_count": len(extraction.candidates),
        "errors": list(extraction.errors),
        "candidates": [
            {
                "entity": c.entity_mention,
                "predicate": c.predicate,
                "value": c.value,
                "label": c.label,
                "quote": c.quote,
                "disposition": c.disposition,
                "reason": c.reason,
            }
            for c in extraction.candidates
        ],
        # Never expose the model's chain-of-thought via GUI or exported report.
        "content_preview": "[推論タグを検出したため非表示]"
        if embedded_thought else raw[:1200],
        "content_truncated": not embedded_thought and len(raw) > 1200,
    }


def export_report(items: list[dict], *, model: str, settings: dict) -> str:
    """A JSON observation report of fictional cases, not a gold-answer score."""
    return json.dumps(
        {"fictional_only": True, "purpose": "review_only_gui_diagnostic",
         "model": model, "settings": settings, "results": items,
         "note": "No database writes. Proposals are not verified facts."},
        ensure_ascii=False, indent=2, default=str,
    )
