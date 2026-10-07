"""Source-neutral contracts for RITSUKO read-only Observation acquisition."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date
from typing import Iterable


AUTO_READ_SOURCES = ("pkb", "web", "finance")
SOURCE_CAPABILITIES = {
    "pkb": "pkb_search",
    "web": "web_research",
    "finance": "finance_read",
}
SOURCE_CONFIDENTIALITY = {
    "pkb": "private",
    "web": "public",
    "finance": "private",
}


def source_capability(source: str) -> str | None:
    return SOURCE_CAPABILITIES.get(str(source or "").strip())


def pending_source_requests(
    session: dict,
    *,
    resource_catalog: dict,
) -> tuple[list[dict], list[dict]]:
    """Group pending MAGI requests by executable Source.

    Returns (executable_groups, unavailable_requests). No fake Observation is
    produced for unavailable Sources.
    """
    if session.get("status") != "waiting_information":
        return [], []

    groups: dict[str, dict] = {}
    unavailable: list[dict] = []
    for raw in (session.get("pending_requests") or []):
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "").strip()
        request_id = str(raw.get("request_id") or "").strip()
        what = str(raw.get("what") or "").strip()
        reason = str(raw.get("reason") or "").strip()

        catalog_item = resource_catalog.get(source) or {}
        executable = (
            source in AUTO_READ_SOURCES
            and bool(catalog_item.get("available"))
            and source_capability(source) is not None
        )
        normalized = {
            "request_id": request_id,
            "source": source,
            "what": what[:4000],
            "reason": reason[:1000],
        }
        if not executable:
            unavailable.append(normalized)
            continue

        group = groups.setdefault(
            source,
            {
                "source": source,
                "request_ids": [],
                "what_parts": [],
                "reasons": [],
            },
        )
        if request_id:
            group["request_ids"].append(request_id)
        if what:
            group["what_parts"].append(what)
        if reason:
            group["reasons"].append(reason)

    executable_groups = []
    for source in AUTO_READ_SOURCES:
        group = groups.get(source)
        if not group:
            continue
        executable_groups.append({
            "source": source,
            "request_ids": list(dict.fromkeys(group["request_ids"]))[:20],
            "what": " / ".join(group["what_parts"])[:4000],
            "reasons": list(dict.fromkeys(group["reasons"]))[:20],
            "capability": source_capability(source),
            "confidentiality": SOURCE_CONFIDENTIALITY[source],
        })
    return executable_groups, unavailable


def _bounded_preview(result: dict) -> list:
    preview: list = []
    for key in ("current", "items", "recent_rows", "categories", "monthly", "hits"):
        value = result.get(key)
        if isinstance(value, list):
            preview.extend(deepcopy(value[:6]))
        if len(preview) >= 12:
            break
    return preview[:12]


def verified_source_observation(
    execution: dict,
    pending_request: dict,
) -> dict | None:
    """Build one bounded verified Observation from a successful read."""
    if str(execution.get("status") or "ok") != "ok":
        return None
    source = str(pending_request.get("source") or "").strip()
    if source not in AUTO_READ_SOURCES:
        return None

    text = str(execution.get("answer") or "").strip()
    if not text:
        return None

    result = deepcopy(execution.get("result") or {})
    observation = {
        "source": source,
        "verified": True,
        "confidentiality": str(
            execution.get("confidentiality")
            or SOURCE_CONFIDENTIALITY[source]
        ),
        "text": text[:4000],
        "responds_to": [
            str(value)
            for value in (pending_request.get("request_ids") or [])
            if str(value).strip()
        ][:20],
        "capability": str(
            execution.get("capability")
            or source_capability(source)
            or ""
        ),
        "result_kind": result.get("result_kind"),
        "result_count": int(execution.get("total") or 0),
        "evidence_preview": _bounded_preview(result),
    }
    metadata = execution.get("source_metadata")
    if isinstance(metadata, dict):
        public_terms = [
            str(value).strip()
            for value in (metadata.get("public_query_terms") or [])
            if str(value).strip()
        ]
        if public_terms:
            observation["public_query_terms"] = public_terms[:20]
        if source == "web":
            observation["source_metadata"] = {
                "provider": metadata.get("provider"),
                "region": metadata.get("region"),
                "web_sources": deepcopy(
                    (metadata.get("web_sources") or [])[:8]
                ),
            }
    return observation


def _flatten_strings(value) -> Iterable[str]:
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 4:
            yield text
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_strings(item)


def web_query_from_request(
    pending_request: dict,
    session: dict,
) -> str:
    """Return a minimal public Web query or reject private-derived leakage.

    Terms obtained only from private Observations may be sent only when the
    Observation explicitly marks them as public-safe query terms.
    """
    query = str(pending_request.get("what") or "").strip()
    if not query:
        raise ValueError("empty_web_query")

    original = str(session.get("user_raw") or "")
    safe_terms: set[str] = set()
    private_terms: set[str] = set()
    for observation in (session.get("observations") or []):
        if not isinstance(observation, dict):
            continue
        if observation.get("confidentiality") not in {"private", "sensitive"}:
            continue
        for value in observation.get("public_query_terms") or []:
            text = str(value).strip()
            if len(text) >= 2:
                safe_terms.add(text.casefold())
        for value in _flatten_strings(
            observation.get("evidence_preview") or []
        ):
            private_terms.add(value)

    folded_query = query.casefold()
    folded_original = original.casefold()
    for private_value in sorted(private_terms, key=len, reverse=True):
        folded_value = private_value.casefold()
        if folded_value not in folded_query:
            continue
        if folded_value in folded_original:
            continue
        if folded_value in safe_terms:
            continue
        raise ValueError("web_query_requires_private_context_approval")

    return query[:1000]


def finance_query_from_request(
    pending_request: dict,
    *,
    today: date | None = None,
) -> str:
    """Normalize relative month wording without changing Finance capability."""
    query = str(pending_request.get("what") or "").strip()
    if not query:
        raise ValueError("empty_finance_query")
    if "先月" not in query:
        return query[:1000]

    current = today or date.today()
    if current.month == 1:
        year, month = current.year - 1, 12
    else:
        year, month = current.year, current.month - 1
    return (f"{year}年{month}月 " + query)[:1000]


def selected_capability_for_batch(executions: list[dict]) -> str | None:
    successful = [
        str(item.get("capability") or "").strip()
        for item in executions
        if str(item.get("status") or "ok") == "ok"
        and str(item.get("capability") or "").strip()
    ]
    unique = list(dict.fromkeys(successful))
    if not unique:
        return None
    if len(unique) == 1:
        return unique[0]
    return "observation_read"
