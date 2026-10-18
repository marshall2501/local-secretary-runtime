# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Connection, Service Billing and Debug HTTP views for the single Secretary UI.

All persisted records and billing logic are owned by existing repositories and
Application services. This module does not create another database or server.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from capabilities.service_billing.service import (
    ServiceBillingError, read_service_billing_snapshot,
)
from capabilities.service_billing.settings import (
    list_service_billing_profiles, upsert_service_billing_profile,
)
from infrastructure.system_debug import (
    runtime_snapshot, environment_snapshot, database_snapshot,
)
from integrations.connections.service_connections import (
    get_service_connection, list_service_connections, upsert_service_connection,
)


class ServiceConnectionInput(BaseModel):
    """Connection settings the user may update on the local Secretary UI."""

    connection_id: str | None = None
    adapter_key: str
    display_name: str = Field(min_length=1, max_length=200)
    endpoint: str | None = None
    account_label: str | None = None
    credential_ref: str | None = None
    capabilities: list[str] | None = None
    connection_type: str | None = None
    connection_role: str | None = None
    config_data: dict | None = None
    auth_data: dict | None = None
    enabled: bool | None = None


class BillingProfileInput(BaseModel):
    """Service Billing profile settings, independent of transport/UI."""

    profile_id: str | None = None
    display_name: str = Field(min_length=1, max_length=200)
    connection_id: str
    enabled: bool = True


def create_daily_overview_router(
    *, connections=None, billing_settings=None,
    database_connection=None, runtime_root: Path | None = None,
    schema_diagram_service=None,
) -> APIRouter:
    """Adapt existing connection/billing/debug Application functions to HTTP.

    Args:
        connections: Registered Service Connections repository.
        billing_settings: Service Billing profile repository.
        database_connection: Optional diagnostic-only DB factory.
        runtime_root: Runtime checkout root for non-mutating process/git info.
        schema_diagram_service: Existing SchemaDiagramService over read-only DB metadata.

    Returns:
        APIRouter: Views requiring no separate NiceGUI process.
    """
    router = APIRouter(prefix="/api/v1", tags=["daily-overview"])
    root = runtime_root or Path(__file__).resolve().parents[2]

    @router.get("/connections")
    def connections_list(include_disabled: bool = True):
        """Show connection names, adapters and configured flags, not auth values."""
        if connections is None:
            raise HTTPException(503, "connections_unavailable")
        rows = list_service_connections(
            connections, include_disabled=include_disabled,
        )
        # Explicit shape: never serialize arbitrary future credential columns.
        fields = (
            "id", "display_name", "adapter_key", "endpoint",
            "account_label", "capabilities", "enabled", "connection_type",
            "connection_role", "auth_configured", "auth_fields", "credential_ref", "config_data",
        )
        return [{key: row.get(key) for key in fields} for row in rows]

    @router.post("/connections")
    def save_connection(body: ServiceConnectionInput):
        """Persist an owner-selected Service Connection using its existing rules."""
        if connections is None:
            raise HTTPException(503, "connections_unavailable")
        try:
            result = upsert_service_connection(
                connections, **body.model_dump(exclude_none=True),
            )
            # Return only the same view shape as GET; auth_data never leaves DB.
            fields = (
                "id", "display_name", "adapter_key", "endpoint",
                "account_label", "capabilities", "enabled", "connection_type",
                "connection_role", "auth_configured", "auth_fields",
                "credential_ref", "config_data",
            )
            return {key: result.get(key) for key in fields}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/billing/profiles")
    def billing_profiles(include_disabled: bool = True):
        """List existing profiles only; viewing does not bootstrap or modify DB."""
        if billing_settings is None:
            raise HTTPException(503, "billing_unavailable")
        return list_service_billing_profiles(
            billing_settings, include_disabled=include_disabled,
        )

    @router.post("/billing/profiles")
    def save_billing_profile(body: BillingProfileInput):
        """Persist a profile through the existing Service Billing Application."""
        if billing_settings is None:
            raise HTTPException(503, "billing_unavailable")
        try:
            return upsert_service_billing_profile(
                billing_settings, **body.model_dump(exclude_none=True),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/billing/profiles/{profile_id}/snapshot")
    def billing_snapshot(profile_id: str):
        """Fetch provider usage on explicit request, through existing service."""
        if connections is None or billing_settings is None:
            raise HTTPException(503, "billing_unavailable")
        profiles = list_service_billing_profiles(
            billing_settings, include_disabled=True,
        )
        matched = next((p for p in profiles if p["id"] == profile_id), None)
        if matched is None:
            raise HTTPException(404, "unknown_billing_profile")
        if not matched.get("enabled") or not matched.get("connection_enabled"):
            raise HTTPException(409, "billing_profile_disabled")
        connection = get_service_connection(connections, matched["connection_id"])
        try:
            return read_service_billing_snapshot(connection)
        except ServiceBillingError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/debug/runtime")
    def debug_runtime():
        """Show current process/checkout details without external calls."""
        return runtime_snapshot(root)

    @router.get("/debug/environment")
    def debug_environment():
        """Show the existing bounded environment diagnostics."""
        return environment_snapshot()

    @router.get("/debug/database")
    def debug_database():
        """Use read-only system diagnostic SQL against the existing database."""
        if database_connection is None:
            raise HTTPException(503, "database_unavailable")
        with database_connection() as db:
            return database_snapshot(
                db, expected_database="secretary",
                expected_user="secretary_daily_runtime",
            )

    @router.get("/debug/er")
    def er_diagram(
        keys_only: bool = True,
        format: Literal["svg", "mermaid"] = "svg",
    ):
        """Create ER visualization on demand from current DB schema metadata."""
        if schema_diagram_service is None:
            raise HTTPException(503, "er_diagram_unavailable")
        selected = "native_svg" if format == "svg" else "native_mermaid"
        result = schema_diagram_service.generate(
            selected,
            enabled={"native_svg": True, "native_mermaid": True},
            options={"keys_only": keys_only},
        )
        if result.status != "ok":
            raise HTTPException(503, "er_diagram_" + result.status)
        return asdict(result)

    return router
