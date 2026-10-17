"""Offline safeguards for the ten-episode PKB corpus import."""
import copy
import unittest
from pathlib import Path

from pkb.episode_intake import digest, load_fixture, validate_corpus

FIXTURE = Path(__file__).resolve().parents[1] / "interfaces" / "workbench" / "fixtures" / "episodes.json"


class EpisodeIntakeTests(unittest.TestCase):
    def setUp(self):
        self.episodes = load_fixture(FIXTURE)

    def test_exact_original_fixture(self):
        self.assertEqual(len(self.episodes), 10)
        self.assertEqual({e["id"] for e in self.episodes},
                         {f"{p}-{n:02d}" for p in ("pc", "rc") for n in range(1, 6)})
        self.assertEqual(sum(e["source_kind"] != "user_statement"
                             for e in self.episodes), 2)

    def test_stable_payload_hash(self):
        self.assertEqual(digest(self.episodes[0]), digest(self.episodes[0]))
        self.assertNotEqual(digest(self.episodes[0]), digest(self.episodes[1]))

    def test_rejects_non_fixture_source(self):
        changed = copy.deepcopy(list(self.episodes))
        changed[0]["source_ref"] = "https://example.invalid/personal-record"
        with self.assertRaises(ValueError):
            validate_corpus({"fictional_only": True,
                             "dataset_id": "pkb-p0-fictional-20260927",
                             "schema_version": 1, "episodes": changed})

    def test_rejects_duplicates_and_naive_time(self):
        changed = copy.deepcopy(list(self.episodes))
        changed[-1] = changed[0]
        with self.assertRaises(ValueError):
            validate_corpus({"fictional_only": True,
                             "dataset_id": "pkb-p0-fictional-20260927",
                             "schema_version": 1, "episodes": changed})
        changed = copy.deepcopy(list(self.episodes))
        changed[0]["recorded_at"] = "2026-09-20T09:00:00"
        with self.assertRaises(ValueError):
            validate_corpus({"fictional_only": True,
                             "dataset_id": "pkb-p0-fictional-20260927",
                             "schema_version": 1, "episodes": changed})

    def test_rejects_partial_corpus(self):
        with self.assertRaisesRegex(ValueError, "exactly ten"):
            validate_corpus({
                "fictional_only": True,
                "dataset_id": "pkb-p0-fictional-20260927",
                "schema_version": 1,
                "episodes": list(self.episodes[:9]),
            })


if __name__ == "__main__":
    unittest.main()
