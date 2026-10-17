"""Validate and load the Workbench-only fictional episode corpus.

An episode is source evidence, not an asserted fact. Independent claim
extraction and the eight-answer gold evaluator are separate later stages.
Never read expected.json while loading: it contains test answers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

SOURCE_KINDS = {"user_statement", "file", "web", "tool", "service"}


def digest(episode: dict) -> str:
    return hashlib.sha256(
        json.dumps(episode, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_corpus(corpus: dict) -> tuple[dict, ...]:
    if (corpus.get("fictional_only") is not True
            or corpus.get("dataset_id") != "pkb-p0-fictional-20260927"
            or corpus.get("schema_version") != 1):
        raise ValueError("Unexpected or non-fictional dataset")
    episodes = corpus.get("episodes")
    if not isinstance(episodes, list) or len(episodes) != 10:
        raise ValueError("The acceptance corpus must contain exactly ten episodes")
    seen = set()
    accepted = []
    for ep in episodes:
        if not isinstance(ep, dict):
            raise ValueError("Episode must be a JSON object")
        episode_id = ep.get("id")
        domain = ep.get("domain")
        if (not isinstance(episode_id, str)
                or not (episode_id.startswith("pc-") and domain == "pc"
                        or episode_id.startswith("rc-") and domain == "rc")
                or episode_id in seen
                or episode_id not in {f"{prefix}-{n:02d}"
                                     for prefix in ("pc", "rc") for n in range(1, 6)}):
            raise ValueError("Invalid, mismatched, or duplicated episode ID")
        seen.add(episode_id)
        if ep.get("source_ref") != "fixture://" + episode_id:
            raise ValueError("Non-fictional Source reference or mismatched episode")
        if ep.get("source_kind") not in SOURCE_KINDS:
            raise ValueError("Unknown Source kind")
        if not isinstance(ep.get("text"), str) or not ep["text"].strip():
            raise ValueError("Empty or invalid original text")
        for field in ("recorded_at", "occurred_at"):
            raw = ep.get(field)
            if not isinstance(raw, str):
                raise ValueError("Missing original timestamp")
            when = datetime.fromisoformat(raw)
            if when.utcoffset() is None:
                raise ValueError("Timestamps require timezone offsets")
        accepted.append(ep)
    return tuple(accepted)


def load_fixture(path: Path) -> tuple[dict, ...]:
    if path.name != "episodes.json" or path.parent.name != "fixtures":
        raise ValueError("Only bundled fictional episodes.json is accepted")
    return validate_corpus(json.loads(path.read_text(encoding="utf-8")))

