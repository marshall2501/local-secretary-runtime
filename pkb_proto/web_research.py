"""Bounded read-only web research adapter for the daily Secretary Core.

The provider is replaceable. The first provider uses ddgs.

The adapter deliberately separates:
- search rank from evidence quality
- bounded page retrieval from factual extraction
- candidate facts from verified truth

No login, form submission, downloads, browser automation, or private-network fetches.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import ipaddress
import os
import re
from urllib.parse import urlparse

from ddgs import DDGS


MAX_QUERY_CHARS = 500
MAX_RESULTS = 5
MAX_FETCHES = 2
MAX_EXCERPT_CHARS = 6000

_DRIVER_INTENT_WORDS = ("ドライバ", "driver")
_LATEST_WORDS = ("最新", "latest", "current", "最新版")
_SUPPORT_WORDS = (
    "drivers and downloads",
    "driver download",
    "latest version",
    "release notes",
    "support",
    "download",
    "downloads",
    "driver",
)
_PREVIOUS_WORDS = ("previous version", "previous versions", "旧版", "過去版")

_VERSION_PATTERNS = (
    re.compile(
        r"(?:adrenalin(?:\s+edition)?|driver(?:\s+version)?|software(?:\s+version)?|version)"
        r"[^0-9]{0,30}(\d{2,4}\.\d{1,3}(?:\.\d{1,4}){1,2})",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d{2}\.\d{1,3}\.\d{1,4})\b"),
)
_DATE_PATTERNS = (
    re.compile(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b"),
    re.compile(r"\b(20\d{2}年\d{1,2}月\d{1,2}日)\b"),
)


@dataclass(frozen=True)
class WebHit:
    rank: int
    evidence_rank: int
    title: str
    url: str
    domain: str
    snippet: str
    fetch_status: str
    excerpt: str | None
    quality_score: int
    authority_hint: str
    version_candidates: list[str]
    version_facts: list[dict]
    date_hints: list[str]


@dataclass(frozen=True)
class WebResearchResult:
    provider: str
    query: str
    region: str
    intent: str
    hits: list[WebHit]
    fact_summary: dict

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "query": self.query,
            "region": self.region,
            "intent": self.intent,
            "total": len(self.hits),
            "hits": [asdict(hit) for hit in self.hits],
            "fact_summary": self.fact_summary,
        }


def _safe_external_url(url: str) -> bool:
    """Reject obviously local/private targets before fetching search results."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _intent_for_query(query: str) -> str:
    lowered = query.lower()
    if any(word in lowered for word in _DRIVER_INTENT_WORDS) and any(
        word in lowered for word in _LATEST_WORDS
    ):
        return "latest_driver"
    return "general"


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _quality_score(intent: str, title: str, url: str, snippet: str) -> tuple[int, str]:
    """Rank likely evidence quality without treating search order as authority."""
    haystack = " ".join((title, url, snippet)).lower()
    score = 0

    if url.startswith("https://"):
        score += 1

    if intent == "latest_driver":
        path = (urlparse(url).path if _safe_external_url(url) else "").lower()
        if "drivers and downloads" in haystack:
            score += 7
        if "latest version" in haystack:
            score += 5
        if "release notes" in haystack:
            score += 4
        if "/drivers/" in path or "/downloads/" in path or "download" in path:
            score += 4
        elif "/support" in path:
            score += 2
        if "driver" in title.lower():
            score += 2
        if any(word in haystack for word in _PREVIOUS_WORDS):
            score -= 6

    if any(word in haystack for word in ("forum", "reddit", "community", "まとめ")):
        score -= 2

    if intent == "latest_driver" and score >= 10:
        hint = "primary_driver_source_candidate"
    elif score >= 5:
        hint = "support_or_documentation_candidate"
    else:
        hint = "general_search_result"
    return score, hint


def _extract_candidates(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    seen: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            if value not in seen:
                seen.append(value)
    return seen[:8]


def _extract_version_facts(text: str) -> list[dict]:
    """Extract version values together with their local semantic label."""
    patterns = (
        (
            "si_driver_version",
            re.compile(
                r"SI\s+Driver(?:\s+Version)?[^0-9]{0,20}"
                r"(\d{2,4}\.\d{1,3}(?:\.\d{1,4}){1,2})",
                re.IGNORECASE,
            ),
        ),
        (
            "adrenalin_version",
            re.compile(
                r"Adrenalin(?:\s+Edition)?[^0-9]{0,30}"
                r"(\d{2,4}\.\d{1,3}(?:\.\d{1,4}){1,2})",
                re.IGNORECASE,
            ),
        ),
        (
            "driver_version",
            re.compile(
                r"(?:Driver(?:\s+Version)?|ドライバー)[^0-9]{0,30}"
                r"(\d{2,4}\.\d{1,3}(?:\.\d{1,4}){1,2})",
                re.IGNORECASE,
            ),
        ),
    )
    facts: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            key = (kind, value)
            if key not in seen:
                seen.add(key)
                facts.append({"kind": kind, "value": value})
    return facts[:12]


def _summarize_version_group(kind: str, by_version: dict[str, list[dict]]) -> dict:
    candidates = []
    for value, sources in by_version.items():
        domains = sorted({row["domain"] for row in sources if row["domain"]})
        candidates.append({
            "value": value,
            "source_count": len(sources),
            "domain_count": len(domains),
            "domains": domains,
            "best_quality_score": max(row["quality_score"] for row in sources),
            "sources": sorted(sources, key=lambda row: row["evidence_rank"]),
        })
    candidates.sort(
        key=lambda row: (
            row["domain_count"],
            row["source_count"],
            row["best_quality_score"],
        ),
        reverse=True,
    )
    if not candidates:
        status = "no_candidate"
        best = None
    elif len(candidates) == 1:
        status = "single_candidate"
        best = candidates[0]["value"]
    elif candidates[0]["source_count"] >= 2 and (
        candidates[0]["source_count"] > candidates[1]["source_count"]
        or candidates[0]["domain_count"] > candidates[1]["domain_count"]
    ):
        status = "leading_consensus"
        best = candidates[0]["value"]
    else:
        status = "conflicting_candidates"
        best = candidates[0]["value"]
    return {
        "kind": kind,
        "status": status,
        "best_candidate": best,
        "candidates": candidates[:8],
    }


def _fact_summary(intent: str, hits: list[WebHit]) -> dict:
    if intent != "latest_driver":
        return {"kind": "none", "status": "not_applicable"}

    current_by_kind: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    historical_by_kind: dict[str, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for hit in hits:
        hit_text = " ".join((hit.title, hit.url, hit.snippet)).lower()
        historical = any(word in hit_text for word in _PREVIOUS_WORDS)
        target = historical_by_kind if historical else current_by_kind
        for fact in hit.version_facts:
            kind = fact["kind"]
            value = fact["value"]
            target[kind][value].append({
                "url": hit.url,
                "title": hit.title,
                "domain": hit.domain,
                "quality_score": hit.quality_score,
                "authority_hint": hit.authority_hint,
                "evidence_rank": hit.evidence_rank,
            })

    groups = [
        _summarize_version_group(kind, versions)
        for kind, versions in current_by_kind.items()
    ]
    priority = {
        "adrenalin_version": 0,
        "driver_version": 1,
        "si_driver_version": 2,
    }
    groups.sort(
        key=lambda row: (
            priority.get(row["kind"], 99),
            -(row["candidates"][0]["best_quality_score"] if row["candidates"] else 0),
        )
    )

    historical_groups = [
        _summarize_version_group(kind, versions)
        for kind, versions in historical_by_kind.items()
    ]
    historical_groups.sort(key=lambda row: priority.get(row["kind"], 99))

    preferred = groups[0] if groups else None
    if not preferred:
        overall_status = "no_version_candidate"
        best = None
        preferred_kind = None
    else:
        overall_status = preferred["status"]
        best = preferred["best_candidate"]
        preferred_kind = preferred["kind"]

    return {
        "kind": "driver_version",
        "status": overall_status,
        "preferred_kind": preferred_kind,
        "best_candidate": best,
        "groups": groups,
        "historical_groups": historical_groups,
    }


def research_web(
    query: str,
    *,
    max_results: int = MAX_RESULTS,
    max_fetches: int = MAX_FETCHES,
    region: str | None = None,
) -> WebResearchResult:
    """Search the public web, rank evidence candidates, then fetch a bounded subset."""
    q = query.strip()
    if not q:
        raise ValueError("web query is empty")
    if len(q) > MAX_QUERY_CHARS:
        raise ValueError("web query is too long")
    if not 1 <= max_results <= MAX_RESULTS:
        raise ValueError("max_results out of range")
    if not 0 <= max_fetches <= MAX_FETCHES:
        raise ValueError("max_fetches out of range")

    resolved_region = region or os.getenv("LSA_WEB_REGION", "jp-jp")
    client = DDGS(timeout=8)
    intent = _intent_for_query(q)
    raw_hits = list(
        client.text(
            q,
            region=resolved_region,
            safesearch="moderate",
            max_results=max_results,
            backend="auto",
        )
    )

    staged = []
    for index, raw in enumerate(raw_hits, start=1):
        url = str(raw.get("href") or "").strip()
        title = str(raw.get("title") or url or "(untitled)").strip()
        snippet = str(raw.get("body") or "").strip()
        score, authority_hint = _quality_score(intent, title, url, snippet)
        staged.append({
            "rank": index,
            "title": title,
            "url": url,
            "domain": _domain(url),
            "snippet": snippet,
            "quality_score": score,
            "authority_hint": authority_hint,
        })

    evidence_order = sorted(
        range(len(staged)),
        key=lambda idx: (
            staged[idx]["quality_score"],
            -staged[idx]["rank"],
        ),
        reverse=True,
    )
    evidence_rank_by_index = {
        idx: evidence_rank
        for evidence_rank, idx in enumerate(evidence_order, start=1)
    }
    fetch_indices = set(evidence_order[:max_fetches])

    hits: list[WebHit] = []
    for idx, row in enumerate(staged):
        fetch_status = "not_fetched"
        excerpt = None
        if not _safe_external_url(row["url"]):
            fetch_status = "blocked_url"
        elif idx in fetch_indices:
            try:
                extracted = client.extract(row["url"], fmt="text_plain")
                text = str(extracted.get("content") or "").strip()
                excerpt = text[:MAX_EXCERPT_CHARS] if text else None
                fetch_status = "fetched" if excerpt else "empty"
            except Exception as exc:
                fetch_status = "fetch_error:" + type(exc).__name__

        evidence_text = " ".join(
            part for part in (row["title"], row["snippet"], excerpt or "") if part
        )
        hits.append(
            WebHit(
                rank=row["rank"],
                evidence_rank=evidence_rank_by_index[idx],
                title=row["title"],
                url=row["url"],
                domain=row["domain"],
                snippet=row["snippet"],
                fetch_status=fetch_status,
                excerpt=excerpt,
                quality_score=row["quality_score"],
                authority_hint=row["authority_hint"],
                version_candidates=_extract_candidates(
                    evidence_text, _VERSION_PATTERNS
                ),
                version_facts=_extract_version_facts(evidence_text),
                date_hints=_extract_candidates(evidence_text, _DATE_PATTERNS),
            )
        )

    evidence_sorted_hits = sorted(hits, key=lambda hit: hit.evidence_rank)
    return WebResearchResult(
        provider="ddgs",
        query=q,
        region=resolved_region,
        intent=intent,
        hits=evidence_sorted_hits,
        fact_summary=_fact_summary(intent, evidence_sorted_hits),
    )
