from unittest import TestCase
from unittest.mock import patch

from application.schema_diagram import (
    SchemaColumn,
    SchemaDiagramService,
    SchemaForeignKey,
    SchemaSnapshot,
    SchemaTable,
)
from infrastructure.postgres.schema_introspection import load_schema_snapshot
from infrastructure.schema_diagram.providers import (
    NativeMermaidProvider,
    OptionalToolProvider,
    render_mermaid,
)


def _snapshot():
    return SchemaSnapshot(
        database="secretary",
        schema="secretary",
        tables=(
            SchemaTable(
                name="entities",
                kind="BASE TABLE",
                columns=(
                    SchemaColumn("id", "uuid", False, primary_key=True),
                    SchemaColumn("name", "text", False),
                ),
                primary_key=("id",),
            ),
            SchemaTable(
                name="claims",
                kind="BASE TABLE",
                columns=(
                    SchemaColumn("id", "uuid", False, primary_key=True),
                    SchemaColumn(
                        "entity_id",
                        "uuid",
                        False,
                        foreign_key=True,
                    ),
                ),
                primary_key=("id",),
            ),
            SchemaTable(
                name="current_claims",
                kind="VIEW",
                columns=(
                    SchemaColumn("entity_id", "uuid", True),
                ),
            ),
        ),
        foreign_keys=(
            SchemaForeignKey(
                constraint_name="claims_entity_id_fkey",
                source_table="claims",
                source_columns=("entity_id",),
                target_table="entities",
                target_columns=("id",),
                update_rule="NO ACTION",
                delete_rule="CASCADE",
            ),
        ),
        views=("current_claims",),
    )


class SchemaDiagramTests(TestCase):
    def test_mermaid_renderer_is_deterministic_and_omits_views(self):
        first = render_mermaid(_snapshot())
        second = render_mermaid(_snapshot())

        self.assertEqual(first, second)
        self.assertIn("erDiagram", first)
        self.assertIn("entities {", first)
        self.assertIn("claims {", first)
        self.assertIn("uuid id PK", first)
        self.assertIn("uuid entity_id FK", first)
        self.assertIn(
            'entities ||--o{ claims : "claims_entity_id_fkey"',
            first,
        )
        self.assertNotIn("current_claims {", first)

    def test_native_provider_reports_counts_and_warning_for_views(self):
        provider = NativeMermaidProvider(_snapshot)
        status = provider.status(enabled=True)
        result = provider.generate()

        self.assertTrue(status.available)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.database, "secretary")
        self.assertEqual(result.schema, "secretary")
        self.assertEqual(result.table_count, 2)
        self.assertEqual(result.relation_count, 1)
        self.assertEqual(result.output_format, "mermaid")
        self.assertTrue(result.warnings)

    def test_service_respects_disabled_and_unavailable_providers(self):
        native = NativeMermaidProvider(_snapshot)
        external = OptionalToolProvider(
            key="tbls",
            display_name="tbls",
            executable="definitely-not-a-real-tbls-binary",
        )
        service = SchemaDiagramService((native, external))

        disabled = service.generate(
            "native_mermaid",
            enabled={"native_mermaid": False},
        )
        unavailable = service.generate(
            "tbls",
            enabled={"tbls": True},
        )

        self.assertEqual(disabled.status, "disabled")
        self.assertEqual(unavailable.status, "unavailable")
        self.assertNotIn("password", " ".join(unavailable.warnings).lower())

    @patch("infrastructure.schema_diagram.providers.shutil.which")
    def test_optional_provider_probe_does_not_execute_tool(self, which):
        which.side_effect = lambda name: f"C:/tools/{name}.exe"
        provider = OptionalToolProvider(
            key="schemacrawler",
            display_name="SchemaCrawler",
            executable="schemacrawler",
            additional_executable="java",
        )

        status = provider.status(enabled=True)
        result = provider.generate()

        self.assertFalse(status.available)
        self.assertIn("dependency detected", status.dependency_status)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("explicitly accepted", result.warnings[0])


class _Cursor:
    def __init__(self):
        self.query = ""
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.query = " ".join(str(query).split())
        self.queries.append((self.query, params))

    def fetchone(self):
        if "current_database()" in self.query:
            return ("secretary",)
        raise AssertionError("unexpected fetchone: " + self.query)

    def fetchall(self):
        if "FROM information_schema.tables" in self.query:
            return [
                ("claims", "BASE TABLE"),
                ("current_claims", "VIEW"),
                ("entities", "BASE TABLE"),
            ]
        if "FROM information_schema.columns" in self.query:
            return [
                ("claims", "id", "uuid", "NO", None, 1),
                ("claims", "entity_id", "uuid", "NO", None, 2),
                ("current_claims", "entity_id", "uuid", "YES", None, 1),
                ("entities", "id", "uuid", "NO", None, 1),
                ("entities", "name", "text", "NO", None, 2),
            ]
        if "FROM information_schema.table_constraints" in self.query:
            return [
                ("claims", "claims_pkey", "PRIMARY KEY", "id", 1),
                ("entities", "entities_pkey", "PRIMARY KEY", "id", 1),
            ]
        if "FROM pg_constraint AS con" in self.query:
            return [
                (
                    "claims_entity_id_fkey",
                    "claims",
                    "entity_id",
                    "entities",
                    "id",
                    1,
                    "a",
                    "c",
                )
            ]
        if "FROM pg_indexes" in self.query:
            return [
                ("claims", "claims_pkey"),
                ("entities", "entities_pkey"),
            ]
        raise AssertionError("unexpected fetchall: " + self.query)


class _Db:
    def __init__(self):
        self.cursor_obj = _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj


class SchemaIntrospectionTests(TestCase):
    def test_metadata_only_introspection_builds_snapshot(self):
        db = _Db()
        snapshot = load_schema_snapshot(lambda: db)

        self.assertEqual(snapshot.database, "secretary")
        self.assertEqual(snapshot.schema, "secretary")
        self.assertEqual(snapshot.table_count, 2)
        self.assertEqual(snapshot.views, ("current_claims",))
        self.assertEqual(snapshot.relation_count, 1)

        claims = next(table for table in snapshot.tables if table.name == "claims")
        entity_id = next(
            column for column in claims.columns if column.name == "entity_id"
        )
        self.assertTrue(entity_id.foreign_key)
        self.assertEqual(claims.primary_key, ("id",))

        rendered_queries = "\n".join(query for query, _ in db.cursor_obj.queries)
        self.assertNotIn("SELECT * FROM secretary.", rendered_queries)
        self.assertNotIn("FROM secretary.claims", rendered_queries)
        self.assertNotIn("FROM secretary.entities", rendered_queries)
