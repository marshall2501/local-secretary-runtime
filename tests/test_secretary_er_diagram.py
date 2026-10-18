"""Unified Secretary Vue ER diagram contract using only fabricated metadata."""
from __future__ import annotations

from xml.etree import ElementTree
import unittest

from fastapi.testclient import TestClient

from application.schema_diagram import (
    SchemaColumn, SchemaDiagramService, SchemaForeignKey,
    SchemaSnapshot, SchemaTable,
)
from infrastructure.schema_diagram.svg_renderer import (
    NativeSvgProvider, render_svg,
)
from infrastructure.schema_diagram.providers import NativeMermaidProvider
from interfaces.api.secretary_server import create_app


def fabricated_snapshot():
    """Build fake schema metadata; never connect to the user's database."""
    return SchemaSnapshot(
        database="secretary", schema="secretary",
        tables=(
            SchemaTable(
                name="tasks", kind="BASE TABLE",
                columns=(
                    SchemaColumn("id", "uuid", False, primary_key=True),
                    SchemaColumn("description", "text", False),
                ),
            ),
            SchemaTable(
                name="results", kind="BASE TABLE",
                columns=(
                    SchemaColumn("id", "uuid", False, primary_key=True),
                    SchemaColumn("task_id", "uuid", False, foreign_key=True),
                ),
            ),
            SchemaTable(name="active_tasks", kind="VIEW"),
        ),
        foreign_keys=(
            SchemaForeignKey(
                constraint_name="results_task_id_fkey",
                source_table="results", source_columns=("task_id",),
                target_table="tasks", target_columns=("id",),
                update_rule="NO ACTION", delete_rule="NO ACTION",
            ),
        ),
        views=("active_tasks",),
    )


class SchemaSvgTests(unittest.TestCase):
    """Native SVG needs no Mermaid package, browser CDN, or table-row SQL."""

    def test_xml_is_valid_keys_and_foreign_links_are_present(self):
        snapshot = fabricated_snapshot()
        xml = render_svg(snapshot, keys_only=True)
        root = ElementTree.fromstring(xml)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertIn("tasks", xml)
        self.assertIn("task_id", xml)
        self.assertIn("results_task_id_fkey", xml)
        self.assertNotIn("description", xml)
        self.assertNotIn("active_tasks", xml)
        self.assertIn("description", render_svg(snapshot, keys_only=False))

    def test_schema_names_are_escaped_in_xml(self):
        snapshot = fabricated_snapshot()
        snapshot = SchemaSnapshot(
            database="private<db>", schema="secretary", foreign_keys=(),
            tables=(SchemaTable(
                name='unsafe<&"', kind="BASE TABLE",
                columns=(SchemaColumn('name<&"', "text", False),),
            ),), views=(),
        )
        svg = render_svg(snapshot, keys_only=False)
        ElementTree.fromstring(svg)
        self.assertNotIn("<db>", svg)
        self.assertNotIn("unsafe<&", svg)
        self.assertIn("&lt;", svg)

    def test_er_api_uses_shared_schema_service_in_one_host(self):
        fake = fabricated_snapshot()
        app = create_app(
            queries=object(),
            schema_diagram_service=SchemaDiagramService((
                NativeSvgProvider(lambda: fake),
                NativeMermaidProvider(lambda: fake),
            )),
        )
        client = TestClient(app, base_url="http://127.0.0.1:8097")
        svg = client.get("/api/v1/debug/er")
        self.assertEqual(svg.status_code, 200, svg.text)
        payload = svg.json()
        self.assertEqual(payload["output_format"], "svg")
        self.assertEqual(payload["table_count"], 2)
        self.assertEqual(payload["relation_count"], 1)
        ElementTree.fromstring(payload["content"])
        self.assertNotIn("description", payload["content"])

        full = client.get("/api/v1/debug/er?keys_only=false")
        self.assertEqual(full.status_code, 200)
        self.assertIn("description", full.json()["content"])

        mermaid = client.get("/api/v1/debug/er?format=mermaid")
        self.assertEqual(mermaid.status_code, 200)
        self.assertEqual(mermaid.json()["output_format"], "mermaid")
        self.assertIn("erDiagram", mermaid.json()["content"])
        self.assertEqual(
            client.get("/api/v1/debug/er?format=unknown").status_code, 422,
        )

    def test_unwired_server_returns_unavailable(self):
        client = TestClient(
            create_app(queries=object()), base_url="http://127.0.0.1:8097",
        )
        self.assertEqual(client.get("/api/v1/debug/er").status_code, 503)


if __name__ == "__main__":
    unittest.main()
