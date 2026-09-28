from __future__ import annotations

import unittest

from pkb_proto.finance_preview import analyze_moneyforward_csv


HEADER = "計算対象,日付,内容,金額（円）,保有金融機関,大項目,中項目,メモ,振替,ID\n"


class FinancePreviewTests(unittest.TestCase):
    def test_moneyforward_preview_summary(self):
        csv_text = (
            HEADER
            + "1,2026/09/01,給与,300000,銀行A,収入,給与,,0,id-1\n"
            + "1,2026/09/02,スーパー,-5000,カードA,食費,食料品,,0,id-2\n"
            + "0,2026/09/03,振替,-10000,銀行A,未分類,未分類,,1,id-3\n"
        )
        preview = analyze_moneyforward_csv(csv_text.encode("utf-8-sig"), "sample.csv")
        self.assertEqual(preview.row_count, 3)
        self.assertEqual(preview.calculation_target_count, 2)
        self.assertEqual(preview.transfer_count, 1)
        self.assertEqual(preview.income_total, 300000)
        self.assertEqual(preview.expense_total, 5000)
        self.assertEqual(preview.net_total, 295000)
        self.assertEqual(preview.duplicate_id_count, 0)
        self.assertEqual(preview.start_date, "2026-09-01")
        self.assertEqual(preview.end_date, "2026-09-03")

    def test_duplicate_external_ids_are_reported(self):
        csv_text = (
            HEADER
            + "1,2026/09/01,A,-100,カードA,食費,その他,,0,dup\n"
            + "1,2026/09/02,B,-200,カードA,食費,その他,,0,dup\n"
        )
        preview = analyze_moneyforward_csv(csv_text.encode("utf-8"))
        self.assertEqual(preview.unique_id_count, 1)
        self.assertEqual(preview.duplicate_id_count, 1)

    def test_wrong_columns_fail_closed(self):
        bad = "日付,金額\n2026/09/01,-100\n".encode("utf-8")
        with self.assertRaisesRegex(ValueError, "想定列と一致"):
            analyze_moneyforward_csv(bad)


if __name__ == "__main__":
    unittest.main()
