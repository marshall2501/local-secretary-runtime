# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Isolated regression tests for legacy-compatible layout persistence."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from interfaces.api.secretary_server import create_app
from interfaces.vue_daily.layout_preferences import (
    default_layout_preferences, read_layout_preferences, save_layout_preferences,
)


class LayoutPreferenceTests(unittest.TestCase):
    """Validate migration, partial-file preservation and command boundary."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ui_preferences.json"

    def test_existing_user_setting_and_unknown_fields_survive_layout_write(self):
        original = {
            "core_goal_mode": True,
            "core_show_judgment_mode": False,
            "core_advisor_model": "owner-model",
            "owner_unknown_setting": {"future": 42},
            "pkb": {"write": False, "pending": False},
        }
        self.path.write_text(json.dumps(original), encoding="utf-8")
        layout = read_layout_preferences(self.path)
        self.assertFalse(layout["pkb"]["write"])
        self.assertFalse(layout["pkb"]["pending"])
        self.assertEqual(layout["core"]["open_limit"], 5)
        layout["core"]["open_limit"] = 3
        layout["entity"]["default_tab"] = "sources"
        layout["visibility"]["pkb"]["search"] = False
        saved = save_layout_preferences(layout, self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertTrue(raw["core_goal_mode"])
        self.assertFalse(raw["core_show_judgment_mode"])
        self.assertEqual(raw["core_advisor_model"], "owner-model")
        self.assertEqual(raw["owner_unknown_setting"], {"future": 42})
        self.assertEqual(saved["core"]["open_limit"], 3)
        self.assertEqual(saved["entity"]["default_tab"], "sources")
        self.assertFalse(saved["visibility"]["pkb"]["search"])

    def test_corruption_and_hidden_all_entity_tabs_never_clobber_user_file(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            read_layout_preferences(self.path)
        with self.assertRaises(json.JSONDecodeError):
            save_layout_preferences(default_layout_preferences(), self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")
        self.path.unlink()
        options = default_layout_preferences()
        options["visibility"]["entity"] = {
            name: False for name in ("overview", "history", "relations", "sources")
        }
        with self.assertRaises(ValueError):
            save_layout_preferences(options, self.path)
        self.assertFalse(self.path.exists())

    def test_read_old_entity_visibility_migration(self):
        self.path.write_text(json.dumps({
            "visibility": {"entity": {"current": False, "events": True,
                                      "relations": True, "history": False}},
            "entity": {"events": True},
        }), encoding="utf-8")
        layout = read_layout_preferences(self.path)
        self.assertTrue(layout["visibility"]["entity"]["history"])
        self.assertFalse(layout["visibility"]["entity"]["overview"])
        self.assertTrue(layout["visibility"]["entity"]["sources"])
        self.assertEqual(layout["entity"]["default_tab"], "history")

    def test_http_read_and_explicit_write_same_file_with_readonly_guard(self):
        with patch.dict(os.environ, {"LSA_UI_PREFERENCES_PATH": str(self.path)}):
            readonly = TestClient(create_app(
                queries=object(), commands=None,
                dist_dir=Path(self.directory.name) / "no-vue-dist",
            ), base_url="http://127.0.0.1:8097")
            options = readonly.get("/api/v1/ui/layout").json()
            self.assertEqual(options["finance"]["recent_limit"], 25)
            self.assertEqual(readonly.post("/api/v1/ui/layout", json=options).status_code, 503)
            self.assertFalse(self.path.exists())
            enabled = TestClient(create_app(
                queries=object(), commands=object(),
                dist_dir=Path(self.directory.name) / "no-vue-dist",
            ), base_url="http://127.0.0.1:8097")
            options["finance"]["recent_limit"] = 50
            posted = enabled.post("/api/v1/ui/layout", json=options)
            self.assertEqual(posted.status_code, 200, posted.text)
            self.assertEqual(posted.json()["finance"]["recent_limit"], 50)
            self.assertEqual(
                enabled.get("/api/v1/ui/layout").json()["finance"]["recent_limit"], 50)
            options["finance"]["recent_limit"] = 75
            self.assertEqual(enabled.post("/api/v1/ui/layout", json=options).status_code, 422)
            self.assertEqual(read_layout_preferences(self.path)["finance"]["recent_limit"], 50)


if __name__ == "__main__":
    unittest.main()
