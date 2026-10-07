"""Application contracts for read-only database schema diagrams."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol


@dataclass(frozen=True)
class SchemaColumn:
    name: str
    data_type: str
    nullable: bool
    default_present: bool = False
    primary_key: bool = False
    foreign_key: bool = False


@dataclass(frozen=True)
class SchemaTable:
    name: str
    kind: str
    columns: tuple[SchemaColumn, ...] = ()
    primary_key: tuple[str, ...] = ()
    unique_constraints: tuple[tuple[str, ...], ...] = ()
    indexes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchemaForeignKey:
    constraint_name: str
    source_table: str
    source_columns: tuple[str, ...]
    target_table: str
    target_columns: tuple[str, ...]
    update_rule: str
    delete_rule: str


@dataclass(frozen=True)
class SchemaSnapshot:
    database: str
    schema: str
    tables: tuple[SchemaTable, ...]
    foreign_keys: tuple[SchemaForeignKey, ...]
    views: tuple[str, ...]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def table_count(self) -> int:
        return sum(1 for table in self.tables if table.kind == "BASE TABLE")

    @property
    def relation_count(self) -> int:
        return len(self.foreign_keys)


@dataclass(frozen=True)
class ProviderStatus:
    key: str
    display_name: str
    enabled: bool
    available: bool
    version: str | None = None
    dependency_status: str = ""


@dataclass(frozen=True)
class SchemaDiagramResult:
    status: str
    provider: str
    database: str = ""
    schema: str = ""
    output_format: str = "text"
    content: str = ""
    table_count: int = 0
    relation_count: int = 0
    duration_ms: int = 0
    generated_at: str = ""
    warnings: tuple[str, ...] = ()
    diagnostics: str = ""


class SchemaDiagramProvider(Protocol):
    key: str
    display_name: str

    def status(self, *, enabled: bool) -> ProviderStatus: ...

    def generate(
        self,
        *,
        options: dict[str, object] | None = None,
    ) -> SchemaDiagramResult: ...


class SchemaDiagramService:
    """Coordinates independent ER-diagram providers without UI dependencies."""

    def __init__(self, providers: tuple[SchemaDiagramProvider, ...]):
        self._providers = {provider.key: provider for provider in providers}

    def provider_statuses(self, enabled: dict[str, bool] | None = None) -> list[ProviderStatus]:
        settings = enabled or {}
        return [
            provider.status(enabled=bool(settings.get(provider.key, False)))
            for provider in self._providers.values()
        ]

    def generate(
        self,
        provider_key: str,
        *,
        enabled: dict[str, bool] | None = None,
        options: dict[str, object] | None = None,
    ) -> SchemaDiagramResult:
        provider = self._providers.get(provider_key)
        if provider is None:
            return SchemaDiagramResult(
                status="unavailable",
                provider=provider_key,
                warnings=("unknown provider",),
            )

        settings = enabled or {}
        status = provider.status(enabled=bool(settings.get(provider_key, False)))
        if not status.enabled:
            return SchemaDiagramResult(
                status="disabled",
                provider=provider_key,
                warnings=("provider is disabled",),
            )
        if not status.available:
            return SchemaDiagramResult(
                status="unavailable",
                provider=provider_key,
                warnings=(status.dependency_status or "provider dependency unavailable",),
            )
        return provider.generate(options=options)
