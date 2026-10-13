"""Provider-native, read-only Web research for MAGI Source requests.

RITSUKO validates the public query and operation grant before invoking this
adapter. Tool replies are evidence *candidates*, not verified truth. This
module never exposes network clients, credentials, or arbitrary URLs to a model.
"""
from __future__ import annotations

import ipaddress
import json
from urllib.parse import quote, urlsplit

import anyio

from capabilities.web_research.web_research import research_web
from integrations.connections.credential_resolver import (
    env_name_to_credential_ref,
    resolve_connection_credential,
)


MAX_WEB_QUERY = 500
MAX_WEB_CITATIONS = 8
MAX_TOOL_READS = 1


def _public_url(value: object) -> str | None:
    """Accept a public HTTPS citation; do not fetch arbitrary model URLs."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return None
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return value
    if not address.is_global:
        return None
    return value


def _cites(items: list[dict], provider: str) -> list[dict]:
    found = []
    seen = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        url = _public_url(row.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        found.append({
            "url": url,
            "title": str(row.get("title") or url)[:200],
            "provider": provider,
            "fetch_status": "provider_cited",
        })
        if len(found) == MAX_WEB_CITATIONS:
            break
    return found


def openai_web_result(outer: dict) -> dict:
    calls = [x for x in (outer.get("output") or [])
             if isinstance(x, dict) and x.get("type") == "web_search_call"
             and x.get("status") == "completed"]
    if not calls:
        return {"status": "no_search", "citations": [], "answer": ""}
    texts = []
    raw = []
    for item in outer.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
            raw.extend(x for x in (part.get("annotations") or [])
                       if isinstance(x, dict) and x.get("type") == "url_citation")
    cites = _cites(raw, "openai")
    return {"status": "ok" if cites and texts else "no_citations",
            "answer": "\n".join(texts)[:2600], "citations": cites,
            "tool_calls": len(calls), "usage": outer.get("usage") or {}}


def gemini_web_result(outer: dict) -> dict:
    candidates = outer.get("candidates") or []
    candidate = next((x for x in candidates if isinstance(x, dict)), {})
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "\n".join(x["text"] for x in parts
                     if isinstance(x, dict) and isinstance(x.get("text"), str))
    meta = candidate.get("groundingMetadata") or {}
    queries = meta.get("webSearchQueries") or []
    sources = [(x.get("web") or {}) for x in (meta.get("groundingChunks") or [])
               if isinstance(x, dict)]
    citations = _cites([{"url": s.get("uri"), "title": s.get("title")}
                        for s in sources], "gemini")
    return {"status": "ok" if text and queries and citations else "no_citations",
            "answer": text[:2600], "citations": citations,
            "tool_calls": len(queries) if isinstance(queries, list) else 0,
            "usage": outer.get("usageMetadata") or {}}


def _ollama_calls(outer: dict, query: str) -> bool:
    message = outer.get("message") or {}
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list) or len(calls) != MAX_TOOL_READS:
        return False
    func = (calls[0] or {}).get("function") or {}
    if func.get("name") != "web_search":
        return False
    args = func.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return False
    return isinstance(args, dict) and args.get("query") == query


async def _provider_search(
    spec: dict, query: str, *, transport, credential_loader, local_search,
) -> dict:
    provider = spec.get("provider")
    model = str(spec.get("model") or "")
    endpoint = str(spec.get("endpoint") or "").rstrip("/")
    timeout = min(max(float(spec.get("timeout_seconds") or 60), 1), 120)
    retries = spec.get("retry_http_codes") or [429, 500, 502, 503, 504]
    repeat = bool(spec.get("retry_within_turn", False))
    if provider in {"openai", "gemini"}:
        ref = spec.get("credential_ref") or env_name_to_credential_ref(
            spec.get("credential_env") or (
                "OPENAI_API_KEY" if provider == "openai" else "GEMINI_API_KEY"
            )
        )
        key = credential_loader(spec.get("connection_id"), ref)
        if not key:
            return {"status": "unavailable", "error": "missing_provider_credential",
                    "provider": provider, "model": model}
        if provider == "openai":
            url = (endpoint or "https://api.openai.com/v1") + "/responses"
            body = {
                "model": model, "store": False, "input": query,
                "instructions": (
                    "Search the public web for this question. Answer in Japanese "
                    "and cite real sources. Do not invent URLs."
                ),
                "tools": [{"type": "web_search", "search_context_size": "medium"}],
                "tool_choice": "required",
                "include": ["web_search_call.action.sources"],
                "max_output_tokens": 1400,
            }
            headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        else:
            url = (endpoint or "https://generativelanguage.googleapis.com/v1beta")
            url += "/models/" + quote(model, safe="") + ":generateContent"
            body = {
                "contents": [{"role": "user", "parts": [{"text": query}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {"maxOutputTokens": 1400},
            }
            headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        outer, _ = await transport(
            "POST", url, headers=headers, json_body=body,
            timeout=timeout, retry_enabled=repeat, retry_http_codes=retries,
        )
        result = openai_web_result(outer) if provider == "openai" else gemini_web_result(outer)
    elif provider == "ollama":
        if not endpoint:
            return {"status": "unavailable", "error": "missing_local_endpoint",
                    "provider": provider, "model": model}
        url = endpoint + "/api/chat"
        messages = [
            {"role": "system", "content": "Use web_search only for the public question. Do not call any other tool."},
            {"role": "user", "content": query},
        ]
        body = {
            "model": model, "stream": False, "think": False, "messages": messages,
            "tools": [{
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the public Web for the approved question.",
                    "parameters": {"type": "object", "required": ["query"],
                                   "properties": {"query": {"type": "string"}}},
                },
            }],
            "options": {"temperature": 0},
        }
        outer, _ = await transport(
            "POST", url, headers={"Content-Type": "application/json"},
            json_body=body, timeout=timeout,
            retry_enabled=repeat, retry_http_codes=retries,
        )
        if not _ollama_calls(outer, query):
            return {"status": "no_tool_call", "provider": provider, "model": model}
        # The model may only request the *already approved* query; never run an
        # arbitrary URL or argument supplied in the tool call.
        web = await anyio.to_thread.run_sync(lambda: local_search(query).as_dict())
        cited = [h for h in web.get("hits") or []
                 if isinstance(h, dict) and h.get("fetch_status") == "fetched"]
        citations = _cites(cited, "ollama")
        if not citations:
            result = {"status": "no_citations", "citations": [], "answer": ""}
        else:
            messages.extend([
                outer["message"],
                {"role": "tool", "tool_name": "web_search",
                 "content": json.dumps({"query": query, "hits": cited[:3]},
                                       ensure_ascii=False)[:7500]},
            ])
            second, _ = await transport(
                "POST", url, headers={"Content-Type": "application/json"},
                json_body={"model": model, "stream": False, "think": False,
                           "messages": messages, "options": {"temperature": 0}},
                timeout=timeout, retry_enabled=repeat, retry_http_codes=retries,
            )
            answer = str((second.get("message") or {}).get("content") or "")[:2600]
            result = {"status": "ok" if answer else "no_answer",
                      "citations": citations, "answer": answer,
                      "tool_calls": 1, "usage": {
                          "prompt_eval_count": outer.get("prompt_eval_count"),
                          "eval_count": outer.get("eval_count"),
                      }}
    else:
        return {"status": "unsupported_provider", "provider": provider, "model": model}
    return {"provider": provider, "model": model,
            "member": spec.get("name"), **result}


async def search_magi_web(
    query: str, member_specs: list[dict], *,
    transport=None, credential_loader=None, local_search=None,
) -> dict:
    """Run each enabled MAGI's own Web capability; return one Source read.

    The caller must already have checked the Task policy and public-query gate.
    Failure of any one model does not suppress other members' cited evidence.
    """
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_WEB_QUERY:
        raise ValueError("invalid_public_web_query")
    if not member_specs:
        return {"status": "error", "result_kind": "web_research", "error_type": "no_member"}
    if transport is None:
        from ritsuko.magi import async_execution
        transport = async_execution.request_json_with_retry
    credential_loader = credential_loader or resolve_connection_credential
    local_search = local_search or research_web
    rows = [None] * len(member_specs)

    async def one(index: int, spec: dict) -> None:
        if not spec.get("enabled", True):
            rows[index] = {"provider": spec.get("provider"),
                           "member": spec.get("name"), "status": "disabled"}
            return
        try:
            rows[index] = await _provider_search(
                spec, query, transport=transport,
                credential_loader=credential_loader, local_search=local_search,
            )
        except Exception as exc:
            rows[index] = {"provider": spec.get("provider"), "member": spec.get("name"),
                           "status": "unavailable", "error": type(exc).__name__}

    async with anyio.create_task_group() as group:
        for i, spec in enumerate(member_specs):
            group.start_soon(one, i, spec)

    outcomes = [row for row in rows if isinstance(row, dict)]
    success = [x for x in outcomes if x.get("status") == "ok"]
    hits = []
    for row in success:
        for citation in row.get("citations") or []:
            if len(hits) < 16:
                hits.append({"title": citation["title"], "url": citation["url"],
                             "provider": row.get("provider"), "member": row.get("member"),
                             "snippet": "", "fetch_status": "provider_cited"})
    notes = []
    for row in success:
        sources = ", ".join(x["url"] for x in (row.get("citations") or [])[:3])
        notes.append(
            str(row.get("member") or row.get("provider")) + ": " +
            str(row.get("answer") or "")[:1300] + "\nSources: " + sources
        )
    return {
        "status": "ok" if hits else "error",
        "result_kind": "web_research",
        "source": "web", "capability": "web_research",
        "confidentiality": "public", "tool": "web", "operation": "research",
        "total": len(hits),
        "answer": "\n\n".join(notes)[:4000],
        "verified_by": "provider_reported_web_citations" if hits else None,
        "result": {"result_kind": "web_research", "provider": "magi_tools",
                   "query": query, "hits": hits, "member_runs": [
                       {"provider": x.get("provider"), "member": x.get("member"),
                        "status": x.get("status"), "tool_calls": x.get("tool_calls", 0),
                        "usage": x.get("usage") or {},
                        "error_type": x.get("error")}
                       for x in outcomes]},
        "source_metadata": {
            "provider": "magi_tools", "region": None,
            "web_sources": hits,
            "member_runs": [
                {"member": x.get("member"), "provider": x.get("provider"),
                 "status": x.get("status"), "tool_calls": x.get("tool_calls", 0)}
                for x in outcomes
            ],
        },
    }
