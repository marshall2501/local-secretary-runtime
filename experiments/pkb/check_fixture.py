"""Validate fictional PKB episodes and optional externally produced evaluation results.

stdlib only. No DB, LLM, network, model downloads, or private file reads.
Usage:
    python experiments/pkb/check_fixture.py
    python experiments/pkb/check_fixture.py --results path/to/answers.json

A citation coverage result is NOT a correctness verdict: manually inspect the
answer text, temporal reasoning, abstentions, and source verification.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_fixture():
    data = read_json(HERE / "episodes.json")
    gold = read_json(HERE / "expected.json")
    assert data["dataset_id"] == gold["dataset_id"]
    assert data["fictional_only"] is True
    episodes = data["episodes"]
    questions = gold["questions"]
    ids = [e["id"] for e in episodes]
    qids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids)) == 10, "10 unique fictional episodes expected"
    assert len(qids) == len(set(qids)) == 8, "8 unique questions expected"
    known = set(ids)
    assert {e["domain"] for e in episodes} == {"pc", "rc"}
    assert {e["source_kind"] for e in episodes} <= {"user_statement", "file", "web"}
    for e in episodes:
        assert e["source_ref"] == "fixture://" + e["id"]
        assert e["text"].strip()
        valid_at = datetime.fromisoformat(e["occurred_at"])
        recorded_at = datetime.fromisoformat(e["recorded_at"])
        assert valid_at <= recorded_at
        assert valid_at.tzinfo is not None and recorded_at.tzinfo is not None
    for q in questions:
        assert q["ask"].strip() and q["expected"].strip()
        assert q["must_cite"] and set(q["must_cite"]) <= known
        assert set(q.get("must_not_promote", [])) <= known
    lookup = {e["id"]: e for e in episodes}
    assert len(gold["corrections"]) == 2
    for corr in gold["corrections"]:
        assert corr["event"] in known and corr["target_episode"] in known
        assert datetime.fromisoformat(lookup[corr["event"]]["recorded_at"]) > datetime.fromisoformat(lookup[corr["target_episode"]]["recorded_at"])
        assert datetime.fromisoformat(corr["effective_at"]).tzinfo is not None
    return data, gold


def check_results(path: Path, gold):
    output = read_json(path)
    assert isinstance(output.get("results"), list)
    rows = output["results"]
    expected = {q["id"]: q for q in gold["questions"]}
    seen = set()
    evaluations = []
    for row in rows:
        qid = row["question_id"]
        assert qid in expected and qid not in seen, f"unknown/duplicate question: {qid}"
        seen.add(qid)
        refs = set(row["cited_episode_ids"])
        assert refs <= {e["id"] for e in read_json(HERE / "episodes.json")["episodes"]}
        required = set(expected[qid]["must_cite"])
        prohibited = set(expected[qid].get("must_not_promote", []))
        promoted = set(row.get("reported_verified_episode_ids", []))
        assert promoted <= refs, "promoted evidence must be cited"
        evaluations.append({
            "question_id": qid,
            "required_citations_missing": sorted(required - refs),
            "prohibited_evidence_promoted": sorted(prohibited & promoted),
            "answer_needs_human_review": True,
        })
    assert seen == set(expected), "provide all 8 question results"
    return evaluations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, help="Optional JSON with all 8 question results")
    args = parser.parse_args()
    data, gold = validate_fixture()
    print(f"PASS fixture: {len(data['episodes'])} episodes, "
          f"{len(gold['questions'])} questions, {len(gold['corrections'])} corrections")
    if args.results:
        report = check_results(args.results, gold)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("Note: citation checks do not verify natural-language answers; review manually.")


if __name__ == "__main__":
    main()
