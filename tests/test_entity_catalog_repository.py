import unittest
from unittest.mock import MagicMock
from uuid import UUID

from infrastructure.postgres.entity_catalog_repository import (
    PostgresEntityCatalogRepository,
)


class EntityCatalogRepositoryTests(unittest.TestCase):
    def _db(self):
        db = MagicMock()
        db.__enter__.return_value = db
        db.__exit__.return_value = False

        tx = MagicMock()
        tx.__enter__.return_value = tx
        tx.__exit__.return_value = False
        db.transaction.return_value = tx

        cur = MagicMock()
        cursor_cm = MagicMock()
        cursor_cm.__enter__.return_value = cur
        cursor_cm.__exit__.return_value = False
        db.cursor.return_value = cursor_cm
        return db, cur

    def test_creates_entity_and_audit_atomically(self):
        db, cur = self._db()
        identifier = UUID("00000000-0000-0000-0000-000000000123")
        cur.fetchall.return_value = []
        cur.fetchone.return_value = (identifier, "サブPC", "pc", "computer")

        result = PostgresEntityCatalogRepository(lambda: db).create_or_get(
            name="サブPC",
            domain="pc",
            entity_type="computer",
            actor="local_user",
        )

        self.assertTrue(result["created"])
        self.assertEqual(result["id"], str(identifier))
        sql = "\n".join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn("INSERT INTO secretary.entities", sql)
        self.assertIn("INSERT INTO secretary.audit_events", sql)
        self.assertIn("pg_advisory_xact_lock", sql)

    def test_reuses_matching_active_entity(self):
        db, cur = self._db()
        identifier = UUID("00000000-0000-0000-0000-000000000123")
        cur.fetchall.return_value = [
            (identifier, "サブPC", "pc", "computer"),
        ]

        result = PostgresEntityCatalogRepository(lambda: db).create_or_get(
            name="サブPC",
            domain="pc",
            entity_type="computer",
            actor="local_user",
        )

        self.assertFalse(result["created"])
        sql = "\n".join(call.args[0] for call in cur.execute.call_args_list)
        self.assertNotIn("INSERT INTO secretary.entities", sql)

    def test_rejects_same_name_with_different_identity(self):
        db, cur = self._db()
        identifier = UUID("00000000-0000-0000-0000-000000000123")
        cur.fetchall.return_value = [
            (identifier, "サブPC", "device", "computer"),
        ]

        with self.assertRaisesRegex(ValueError, "別のdomain/type"):
            PostgresEntityCatalogRepository(lambda: db).create_or_get(
                name="サブPC",
                domain="pc",
                entity_type="computer",
                actor="local_user",
            )


if __name__ == "__main__":
    unittest.main()
