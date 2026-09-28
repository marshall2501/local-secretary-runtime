"""Read-only bundled fictional episodes for the developer UI (no DB dependency).

The database intake service has its own stricter transaction/Source contract.
Both reject arbitrary user files and never load expected.json into LLM context.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

BUNDLED_FIXTURE = Path(__file__).parent / "fixtures" / "episodes.json"


def load_fictional_episodes(path: Path = BUNDLED_FIXTURE) -> tuple[dict, ...]:
    path = Path(path).resolve()
    if path != BUNDLED_FIXTURE.resolve():
        raise ValueError("Workbench only accepts its bundled fictional corpus")
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if (corpus.get("fictional_only") is not True
            or corpus.get("dataset_id") != "pkb-p0-fictional-20260927"
            or corpus.get("schema_version") != 1):
        raise ValueError("Unrecognized or non-fictional corpus")
    episodes = corpus.get("episodes")
    if not isinstance(episodes, list) or len(episodes) != 10:
        raise ValueError("Expected exactly ten fictional episodes")
    seen = set()
    for ep in episodes:
        if not isinstance(ep, dict):
            raise ValueError("Invalid episode")
        eid = ep.get("id")
        if (eid not in {f"{domain}-{n:02d}" for domain in ("pc", "rc")
                        for n in range(1, 6)}
                or eid in seen or eid.split("-")[0] != ep.get("domain")
                or ep.get("source_ref") != f"fixture://{eid}"
                or ep.get("source_kind") not in
                    ("user_statement", "file", "web", "tool", "service")
                or not isinstance(ep.get("text"), str) or not ep["text"].strip()):
            raise ValueError("Invalid or duplicated fictional episode")
        seen.add(eid)
        for field in ("recorded_at", "occurred_at"):
            value = ep.get(field)
            if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is None:
                raise ValueError("Timestamp with timezone required")
    return tuple(episodes)
