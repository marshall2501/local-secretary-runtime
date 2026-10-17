import unittest
from types import SimpleNamespace

from config.runtime_database import (
    allowed_daily_connection,
    connection_mode,
    source_ref,
    source_ref_allowed,
)


class RuntimeDatabaseBoundaryTests(unittest.TestCase):
    def db(self, database, user, host="127.0.0.1"):
        return SimpleNamespace(info=SimpleNamespace(dbname=database, user=user, host=host))

    def test_production_daily_pair_uses_local_source_refs(self):
        db = self.db("secretary", "secretary_daily_runtime")
        self.assertEqual(connection_mode(db), "production")
        self.assertTrue(allowed_daily_connection(db))
        self.assertEqual(source_ref(db, "daily-pkb", "abc"), "local://daily-pkb/abc")
        self.assertTrue(source_ref_allowed(db, "local://daily-pkb/abc"))
        self.assertFalse(source_ref_allowed(db, "fixture://daily-pkb/abc"))

    def test_crossed_admin_and_remote_pairs_are_rejected(self):
        cases = (
            self.db("secretary", "secretary_admin"),
            self.db("secretary", "postgres"),
            self.db("secretary_rebuild_unapproved", "secretary_daily_runtime"),
            self.db("secretary", "secretary_daily_runtime", "192.0.2.1"),
        )
        for db in cases:
            with self.subTest(info=db.info):
                self.assertIsNone(connection_mode(db))
                self.assertFalse(allowed_daily_connection(db))


if __name__ == "__main__":
    unittest.main()
