"""Normalize bounded read-only capability results for RITSUKO."""
from __future__ import annotations


def execute_core_read(
    capability: str,
    text: str,
    *,
    pkb_search,
    finance_read,
    web_research,
    pkb_answer,
    finance_answer,
    web_answer,
) -> dict:
    if capability == "pkb_search":
        result = pkb_search(text)
        return {
            "capability": capability, "result": result,
            "answer": pkb_answer(result), "total": int(result.get("total") or 0),
            "tool": "pkb", "operation": "search", "source_slug": "pkb-search",
            "citation": "Secretary Core read-only PKB search result",
            "verified_by": "deterministic_pkb_query",
            "status": "ok",
            "confidentiality": "private",
        }
    if capability == "finance_read":
        result = finance_read(text)
        return {
            "capability": capability, "result": result,
            "answer": finance_answer(result), "total": int(result.get("total") or 0),
            "tool": "finance", "operation": "summary", "source_slug": "finance-read",
            "citation": "Secretary Core read-only finance summary",
            "verified_by": "deterministic_finance_query",
            "status": "ok",
            "confidentiality": "private",
        }
    if capability == "web_research":
        result = web_research(text)
        return {
            "capability": capability, "result": result,
            "answer": web_answer(result), "total": int(result.get("total") or 0),
            "tool": "web", "operation": "research", "source_slug": "web-research",
            "citation": "Secretary Core bounded read-only web research result",
            "verified_by": "bounded_web_retrieval",
            "status": "ok",
            "confidentiality": "public",
            "source_metadata": {
                "provider": result.get("provider"), "region": result.get("region"),
                "web_sources": [
                    {
                        "rank": hit.get("rank"), "title": hit.get("title"),
                        "url": hit.get("url"), "snippet": hit.get("snippet"),
                        "fetch_status": hit.get("fetch_status"),
                        "evidence_rank": hit.get("evidence_rank"),
                        "quality_score": hit.get("quality_score"),
                        "authority_hint": hit.get("authority_hint"),
                        "authority_level": hit.get("authority_level"),
                        "version_candidates": hit.get("version_candidates"),
                        "version_facts": hit.get("version_facts"),
                        "date_hints": hit.get("date_hints"),
                    }
                    for hit in (result.get("hits") or [])
                ],
            },
        }
    raise ValueError("Unsupported Core read capability")
