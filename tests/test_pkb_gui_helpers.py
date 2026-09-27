"""Offline checks for GUI diagnostics; does not open a desktop window."""
import json
import unittest

from pkb_proto.extraction_service import Extraction
from pkb_proto.gui_helpers import analyze_reply, export_report


EP = {"id": "pc-01", "text": "サブPCの架空更新。"}


class GuiHelpersTests(unittest.TestCase):
    def test_empty_content_is_diagnosed_without_fake_success(self):
        result = analyze_reply(
            EP, {"model": "fixture-model", "done_reason": "length",
                 "eval_count": 1100, "message": {"content": "", "thinking": "private"}},
            Extraction("pc-01", (), ("invalid_json",)),
        )
        self.assertEqual(result["json_parse"].split(":")[0], "invalid_json")
        self.assertEqual(result["done_reason"], "length")
        self.assertEqual(result["thinking_length"], 7)
        self.assertEqual(result["candidate_count"], 0)
        self.assertNotIn("private", export_report([result], model="fixture", settings={}))

    def test_valid_json_keeps_metadata(self):
        result = analyze_reply(
            EP, {"message": {"content": '{"candidates":[]}'},
                 "done_reason": "stop", "eval_count": 19},
            Extraction("pc-01", (), ()),
        )
        self.assertEqual(result["json_parse"], "valid_json")
        self.assertEqual(result["json_type"], "dict")
        self.assertEqual(result["candidate_count"], 0)

    def test_embedded_think_is_not_displayed_or_exported(self):
        result = analyze_reply(
            EP, {"message": {"content": "<think>secret reasoning</think>"}},
            Extraction("pc-01", (), ("invalid_json",)),
        )
        self.assertTrue(result["content_begins_think_tag"])
        self.assertNotIn("secret reasoning", result["content_preview"])
        self.assertNotIn("secret reasoning",
                         export_report([result], model="fixture", settings={}))

    def test_export_marks_fictional_and_not_verified(self):
        result = analyze_reply(
            EP, {"message": {"content": '{"candidates":[]}'}, "done_reason": "stop"},
            Extraction("pc-01", (), ()),
        )
        report = json.loads(export_report([result], model="fixture", settings={}))
        self.assertTrue(report["fictional_only"])
        self.assertEqual(report["results"][0]["episode"], "pc-01")
        self.assertIn("No database writes", report["note"])


if __name__ == "__main__":
    unittest.main()
