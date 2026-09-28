from __future__ import annotations

import unittest
from types import SimpleNamespace

from pkb_proto.entity_detail import _guard


class EntityDetailTests(unittest.TestCase):
    def test_guard_accepts_isolated_writer(self):
        db = SimpleNamespace(info=SimpleNamespace(
            dbname="secretary_pkb_proto_20260927",
            user="secretary_pkb_proto_writer_20260927",
            host="127.0.0.1",
        ))
        _guard(db)

    def test_guard_rejects_wrong_database(self):
        db = SimpleNamespace(info=SimpleNamespace(
            dbname="secretary",
            user="secretary_pkb_proto_writer_20260927",
            host="127.0.0.1",
        ))
        with self.assertRaisesRegex(ValueError, "non-isolated"):
            _guard(db)


if __name__ == "__main__":
    unittest.main()
