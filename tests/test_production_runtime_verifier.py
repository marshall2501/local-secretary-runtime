import unittest
from unittest.mock import patch

from scripts.db import verify_production_runtime as verifier


class ProductionRuntimeVerifierContractTests(unittest.TestCase):
    def test_live_port_is_rejected(self):
        with patch.object(verifier, "_configured_live_port", return_value=5432):
            with self.assertRaisesRegex(RuntimeError, "live PostgreSQL port"):
                verifier._validate_disposable_target(5432, 5432)

    def test_supplied_live_port_must_match_configuration(self):
        with patch.object(verifier, "_configured_live_port", return_value=5432):
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                verifier._validate_disposable_target(55432, 15432)

    def test_expected_database_and_role_are_required(self):
        verifier._validate_runtime_identity("secretary", "secretary_daily_runtime")
        for database, user in (
            ("secretary", "secretary_admin"),
            ("secretary_pkb_proto_20260927", "secretary_daily_runtime"),
        ):
            with self.subTest(database=database, user=user):
                with self.assertRaisesRegex(RuntimeError, "wrong database identity"):
                    verifier._validate_runtime_identity(database, user)


if __name__ == "__main__":
    unittest.main()
