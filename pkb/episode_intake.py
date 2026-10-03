"""Lossless, fictional-only episode intake. This does NOT turn prose into Claims.

An episode is source evidence, not an asserted fact. Independent claim
extraction and the eight-answer gold evaluator are separate later stages.
Never read expected.json while importing: it contains test answers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from psycopg.types.json import Jsonb

DBNAME = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"
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


def ingest(db, episodes: tuple[dict, ...]) -> tuple[int, int]:
    info = db.info
    if (info.dbname != DBNAME or info.host not in ("127.0.0.1", "localhost", "::1")
            or info.user != WRITER):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if len(episodes) != 10:
        raise ValueError("Incomplete episode corpus")
    # Revalidate all records before opening the DB transaction.
    validate_corpus({"fictional_only": True, "dataset_id": "pkb-p0-fictional-20260927",
                     "schema_version": 1, "episodes": list(episodes)})
    inserted = 0
    replayed = 0
    with db.transaction(), db.cursor() as cur:
        for ep in episodes:
            ep_id = ep["id"]
            checksum = digest(ep)
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                        ("pkb-fixture:" + ep_id,))
            cur.execute(
                """SELECT payload_sha256 FROM secretary.pkb_episode_receipts
                   WHERE episode_id=%s""", (ep_id,),
            )
            existing = cur.fetchone()
            if existing:
                if existing[0] != checksum:
                    raise ValueError("Changed content for a previously imported episode")
                replayed += 1
                continue
            recorded = datetime.fromisoformat(ep["recorded_at"])
            cur.execute(
                """INSERT INTO secretary.sources
                   (source_type, uri, citation, retrieved_at, recorded_at,
                    confidentiality, metadata)
                   VALUES (%s,%s,%s,%s,%s,'private',%s)
                   RETURNING id""",
                (
                    ep["source_kind"], ep["source_ref"], ep_id, recorded, recorded,
                    Jsonb({
                        "fictional_only": True,
                        "dataset_id": "pkb-p0-fictional-20260927",
                        "episode_id": ep_id,
                        "domain": ep["domain"],
                        "original_text": ep["text"],
                        "occurred_at": ep["occurred_at"],
                        "interpretation_state": "uninterpreted",
                    }),
                ),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO secretary.pkb_episode_receipts
                   (episode_id,source_id,payload_sha256,domain,interpretation_state)
                   VALUES (%s,%s,%s,%s,'uninterpreted')""",
                (ep_id, source_id, checksum, ep["domain"]),
            )
            inserted += 1
    return inserted, replayed
