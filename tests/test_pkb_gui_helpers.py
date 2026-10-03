"""Offline checks for GUI diagnostics; does not open a desktop window."""
import json
import unittest

from pkb.extraction_service import Extraction
from interfaces.workbench.gui_helpers import analyze_reply, export_report


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

    def test_elapsed_time_survives_json_export(self):
        result = analyze_reply(
            EP, {"message": {"content": '{"candidates":[]}'},
                 "done_reason": "stop"},
            Extraction("pc-01", (), ()),
        )
        result["elapsed_seconds"] = 12.345
        exported = json.loads(export_report([result], model="fixture", settings={}))
        self.assertEqual(exported["results"][0]["elapsed_seconds"], 12.345)

    def test_copy_button_copies_exact_report_without_opening_gui(self):
        from pkb_proto.gui import Workbench

        class FakeRoot:
            def __init__(self):
                self.clipboard = None
                self.updated = False

            def clipboard_clear(self):
                self.clipboard = None

            def clipboard_append(self, value):
                self.clipboard = value

            def update_idletasks(self):
                self.updated = True

        class FakeStatus:
            def __init__(self):
                self.text = ""

            def set(self, value):
                self.text = value

        class FakeWorkbench:
            def __init__(self):
                self.results = [{"episode": "pc-01", "elapsed_seconds": 3.2}]
                self.root = FakeRoot()
                self.status_var = FakeStatus()

            def report_json(self):
                return '{"fictional_only": true, "results": [{"elapsed_seconds": 3.2}]}'

        fake = FakeWorkbench()
        Workbench.copy_report(fake)
        self.assertEqual(fake.root.clipboard, fake.report_json())
        self.assertTrue(fake.root.updated)
        self.assertIn("コピー", fake.status_var.text)

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
