"""Bounded read-only web research adapter for the daily Secretary Core.

Initial implementation intentionally keeps the surface small:
- explicit text search only
- at most 5 search hits
- at most 2 fetched pages
- page excerpts truncated before persistence
- no login, form submission, downloads, or browser automation

The provider is replaceable. The first provider uses ddgs.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import ipaddress
import os
from urllib.parse import urlparse

from ddgs import DDGS


MAX_QUERY_CHARS = 500
MAX_RESULTS = 5
MAX_FETCHES = 2
MAX_EXCERPT_CHARS = 6000


@dataclass(frozen=True)
class WebHit:
    rank: int
    title: str
    url: str
    snippet: str
    fetch_status: str
    excerpt: str | None


@dataclass(frozen=True)
class WebResearchResult:
    provider: str
    query: str
    region: str
    hits: list[WebHit]

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "query": self.query,
            "region": self.region,
            "total": len(self.hits),
            "hits": [asdict(hit) for hit in self.hits],
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


def research_web(
    query: str,
    *,
    max_results: int = MAX_RESULTS,
    max_fetches: int = MAX_FETCHES,
    region: str | None = None,
) -> WebResearchResult:
    """Search the public web and fetch a small number of result pages."""
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
    raw_hits = client.text(
        q,
        region=resolved_region,
        safesearch="moderate",
        max_results=max_results,
        backend="auto",
    )

    hits: list[WebHit] = []
    fetched = 0
    for index, raw in enumerate(raw_hits, start=1):
        url = str(raw.get("href") or "").strip()
        title = str(raw.get("title") or url or "(untitled)").strip()
        snippet = str(raw.get("body") or "").strip()
        fetch_status = "not_fetched"
        excerpt = None

        if fetched < max_fetches and _safe_external_url(url):
            try:
                extracted = client.extract(url, fmt="text_plain")
                text = str(extracted.get("content") or "").strip()
                excerpt = text[:MAX_EXCERPT_CHARS] if text else None
                fetch_status = "fetched" if excerpt else "empty"
            except Exception as exc:  # provider/network failures stay bounded evidence
                fetch_status = "fetch_error:" + type(exc).__name__
            fetched += 1
        elif not _safe_external_url(url):
            fetch_status = "blocked_url"

        hits.append(
            WebHit(
                rank=index,
                title=title,
                url=url,
                snippet=snippet,
                fetch_status=fetch_status,
                excerpt=excerpt,
            )
        )

    return WebResearchResult(
        provider="ddgs",
        query=q,
        region=resolved_region,
        hits=hits,
    )
