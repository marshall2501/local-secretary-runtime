"""Transport-only client for MAGI members.

It sends a RITSUKO-created envelope to an assigned model and returns the model
response. It never chooses tools, mutates Tasks, or performs PKB/Web reads.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ritsuko_magi_protocol import MAGI_RESPONSE_SCHEMA, SYSTEM_INSTRUCTION, validate_analysis_result

OLLAMA = "http://127.0.0.1:11434"
PREFERRED_MODELS = ("gemma3:12b", "llama3.1:8b", "qwen3.5:9b")

def list_chat_models(timeout: float = 3.0) -> list[str]:
    req=Request(OLLAMA + "/api/tags",method="GET")
    with urlopen(req,timeout=timeout) as resp:
        data=json.load(resp)
    models=data.get("models") if isinstance(data,dict) else None
    if not isinstance(models,list):
        raise ValueError("Unexpected Ollama model-list response")
    result=[]
    for item in models:
        name=item.get("name") if isinstance(item,dict) else None
        if isinstance(name,str) and name and not name.split(":",1)[0].endswith("-embed-text"):
            result.append(name)
    return result

def choose_model(models: list[str], requested: str | None = None) -> str:
    configured=(requested or "").strip()
    if configured:
        if configured not in models:
            raise ValueError("Configured MAGI model is not installed")
        return configured
    for preferred in PREFERRED_MODELS:
        if preferred in models:
            return preferred
    if not models:
        raise ValueError("No local chat model is installed")
    return models[0]

def call_member(request_envelope: dict, *, member_name: str, model: str, timeout: float = 60.0) -> dict:
    if (request_envelope.get("magi_member") or {}).get("name") != member_name:
        raise ValueError("member_assignment_mismatch")
    assignment={"member":member_name,"provider":"ollama","model":model}
    payload={
        "model":model,
        "stream":False,
        "format":MAGI_RESPONSE_SCHEMA,
        "messages":[
            {"role":"system","content":SYSTEM_INSTRUCTION},
            {"role":"user","content":json.dumps(request_envelope,ensure_ascii=False)},
        ],
        "options":{"temperature":0,"num_predict":1800},
    }
    try:
        req=Request(
            OLLAMA + "/api/chat",
            data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type":"application/json"},
            method="POST",
        )
        with urlopen(req,timeout=timeout) as resp:
            outer=json.load(resp)
        message=outer.get("message") if isinstance(outer,dict) else None
        raw=message.get("content") if isinstance(message,dict) else None
        # Metadata only: never store or expose the model's Thinking text.
        thinking=message.get("thinking") if isinstance(message,dict) else None
        diagnostic={
            "raw_length":len(raw) if isinstance(raw,str) else 0,
            "thinking_length":len(thinking) if isinstance(thinking,str) else 0,
            "done_reason":outer.get("done_reason") if isinstance(outer,dict) else None,
            "eval_count":outer.get("eval_count") if isinstance(outer,dict) else None,
        }
        if not isinstance(raw,str):
            return {"status":"invalid","assignment":assignment,"request_envelope":request_envelope,
                    "response":None,"validation_errors":["missing_model_content"],
                    "diagnostic":diagnostic}
        try:
            parsed=json.loads(raw)
        except (TypeError,ValueError):
            return {"status":"invalid","assignment":assignment,"request_envelope":request_envelope,
                    "response":None,"validation_errors":["invalid_json"],
                    "diagnostic":diagnostic}
        errors=validate_analysis_result(parsed,request_envelope)
        return {"status":"ok" if not errors else "invalid","assignment":assignment,
                "request_envelope":request_envelope,"response":parsed,
                "validation_errors":errors,
                "diagnostic":diagnostic}
    except (OSError,HTTPError,URLError,TimeoutError,ValueError,json.JSONDecodeError) as exc:
        return {"status":"unavailable","assignment":assignment,"request_envelope":request_envelope,
                "response":None,"validation_errors":[type(exc).__name__],
                "diagnostic":{"error":str(exc)[:240]}}
