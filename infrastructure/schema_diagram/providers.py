"""ER diagram providers used by the read-only debug page."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import shutil
import time

from application.schema_diagram import (
    ProviderStatus,
    SchemaDiagramResult,
    SchemaSnapshot,
)


_SAFE_ID = re.compile(r"[^A-Za-z0-9_]")


def _mermaid_id(value: str) -> str:
    cleaned = _SAFE_ID.sub("_", value)
    if not cleaned:
        return "unnamed"
    if cleaned[0].isdigit():
        return "_" + cleaned
    return cleaned


def _mermaid_type(value: str) -> str:
    return _mermaid_id(value.replace(" ", "_").replace("[]", "_array"))


def render_mermaid(snapshot: SchemaSnapshot, *, keys_only: bool = False) -> str:
    """Render a deterministic Mermaid erDiagram from a normalized snapshot."""
    lines = ["erDiagram"]
    base_tables = [table for table in snapshot.tables if table.kind == "BASE TABLE"]

    for table in sorted(base_tables, key=lambda item: item.name):
        table_id = _mermaid_id(table.name)
        lines.append(f"    {table_id} {{")
        columns = table.columns
        if keys_only:
            columns = tuple(
                column
                for column in columns
                if column.primary_key or column.foreign_key
            )
        for column in columns:
            flags = []
            if column.primary_key:
                flags.append("PK")
            if column.foreign_key:
                flags.append("FK")
            suffix = (" " + ", ".join(flags)) if flags else ""
            lines.append(
                f"        {_mermaid_type(column.data_type)} "
                f"{_mermaid_id(column.name)}{suffix}"
            )
        lines.append("    }")

    for foreign_key in sorted(
        snapshot.foreign_keys,
        key=lambda item: (item.target_table, item.source_table, item.constraint_name),
    ):
        target = _mermaid_id(foreign_key.target_table)
        source = _mermaid_id(foreign_key.source_table)
        label = _SAFE_ID.sub("_", foreign_key.constraint_name)
        lines.append(f'    {target} ||--o{{ {source} : "{label}"')

    return "\n".join(lines) + "\n"


class NativeMermaidProvider:
    key = "native_mermaid"
    display_name = "Native + Mermaid"

    def __init__(self, snapshot_loader, *, keys_only: bool = False):
        self._snapshot_loader = snapshot_loader
        self._keys_only = keys_only

    def status(self, *, enabled: bool) -> ProviderStatus:
        return ProviderStatus(
            key=self.key,
            display_name=self.display_name,
            enabled=enabled,
            available=True,
            version="built-in",
            dependency_status="psycopg + NiceGUI Mermaid",
        )

    def generate(
        self,
        *,
        options: dict[str, object] | None = None,
    ) -> SchemaDiagramResult:
        started = time.perf_counter()
        snapshot = self._snapshot_loader()
        requested = options or {}
        keys_only = bool(requested.get("keys_only", self._keys_only))
        content = render_mermaid(snapshot, keys_only=keys_only)
        elapsed = int((time.perf_counter() - started) * 1000)
        return SchemaDiagramResult(
            status="ok",
            provider=self.key,
            database=snapshot.database,
            schema=snapshot.schema,
            output_format="mermaid",
            content=content,
            table_count=snapshot.table_count,
            relation_count=snapshot.relation_count,
            duration_ms=elapsed,
            generated_at=snapshot.generated_at,
            warnings=(
                ("views are listed in metadata but omitted from FK ER rendering",)
                if snapshot.views
                else ()
            ),
        )


class OptionalToolProvider:
    """Probe-only provider until the local dependency is explicitly approved.

    No tool installation or credential-bearing subprocess is attempted here.
    """

    def __init__(
        self,
        *,
        key: str,
        display_name: str,
        executable: str | None = None,
        python_package: str | None = None,
        additional_executable: str | None = None,
    ):
        self.key = key
        self.display_name = display_name
        self._executable = executable
        self._python_package = python_package
        self._additional_executable = additional_executable

    def _probe(self) -> tuple[bool, str | None, str]:
        version = None
        missing: list[str] = []

        if self._executable and shutil.which(self._executable) is None:
            missing.append(self._executable)
        if self._additional_executable and shutil.which(self._additional_executable) is None:
            missing.append(self._additional_executable)

        if self._python_package:
            if importlib.util.find_spec(self._python_package) is None:
                missing.append(self._python_package)
            else:
                try:
                    version = importlib.metadata.version(self._python_package)
                except importlib.metadata.PackageNotFoundError:
                    version = None

        if missing:
            return False, version, "missing: " + ", ".join(missing)
        return True, version, "dependency detected; execution awaits approved local setup"

    def status(self, *, enabled: bool) -> ProviderStatus:
        dependencies_found, version, detail = self._probe()
        if dependencies_found:
            detail = (
                "dependency detected; execution awaits approved local setup "
                "and safe credential handoff"
            )
        return ProviderStatus(
            key=self.key,
            display_name=self.display_name,
            enabled=enabled,
            available=False,
            version=version,
            dependency_status=detail,
        )

    def generate(
        self,
        *,
        options: dict[str, object] | None = None,
    ) -> SchemaDiagramResult:
        return SchemaDiagramResult(
            status="unavailable",
            provider=self.key,
            warnings=(
                "external provider execution is not enabled until its local dependency "
                "and safe credential handoff are explicitly accepted",
            ),
        )
