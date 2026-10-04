import unittest
from unittest.mock import MagicMock, patch

from scripts.db.transfer_common import copy_query_rows, one


class TransferCommonExecutionTests(unittest.TestCase):
    def test_scalar_query_with_literal_percent_does_not_pass_empty_params(self):
        cur = MagicMock()
        cur.fetchone.return_value = (1,)

        result = one(cur, "SELECT 'fixture://daily-pkb/%'")

        self.assertEqual(result, 1)
        self.assertEqual(len(cur.execute.call_args.args), 1)

    def test_scalar_query_with_params_passes_params(self):
        cur = MagicMock()
        cur.fetchone.return_value = (1,)

        result = one(cur, "SELECT %s", (1,))

        self.assertEqual(result, 1)
        self.assertEqual(cur.execute.call_args.args[1], (1,))

    def test_copy_query_without_source_args_preserves_literal_percent(self):
        source = MagicMock()
        target = MagicMock()
        source.fetchall.return_value = []

        with patch(
            "scripts.db.transfer_common.relation_columns",
            return_value=(("id",), {"id": "uuid"}),
        ):
            result = copy_query_rows(
                source,
                target,
                table="entities",
                key_columns=("id",),
                source_query_template=(
                    "SELECT {columns} FROM secretary.entities t "
                    "WHERE t.name LIKE 'daily-pkb-%'"
                ),
            )

        self.assertEqual(result["source"], 0)
        self.assertEqual(len(source.execute.call_args.args), 1)


if __name__ == "__main__":
    unittest.main()
