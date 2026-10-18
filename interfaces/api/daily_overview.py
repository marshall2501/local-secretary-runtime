# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Connection, Service Billing and Debug HTTP views for the single Secretary UI.

All persisted records and billing logic are owned by existing repositories and
Application services. This module does not create another database or server.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from capabilities.service_billing.service import (
    ServiceBillingError, read_service_billing_snapshot,
)
from capabilities.service_billing.settings import list_service_billing_profiles
from infrastructure.system_debug import (
    runtime_snapshot, environment_snapshot, database_snapshot,
)
from integrations.connections.service_connections import (
    get_service_connection, list_service_connections,
)


def create_daily_overview_router(
    *, connections=None, billing_settings=None,
    database_connection=None, runtime_root: Path | None = None,
) -> APIRouter:
    """Adapt existing connection/billing/debug Application functions to HTTP.

    Args:
        connections: Registered Service Connections repository.
        billing_settings: Service Billing profile repository.
        database_connection: Optional diagnostic-only DB factory.
        runtime_root: Runtime checkout root for non-mutating process/git info.

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
            "connection_role", "auth_configured", "auth_fields",
        )
        return [{key: row.get(key) for key in fields} for row in rows]

    @router.get("/billing/profiles")
    def billing_profiles(include_disabled: bool = True):
        """List existing profiles only; viewing does not bootstrap or modify DB."""
        if billing_settings is None:
            raise HTTPException(503, "billing_unavailable")
        return list_service_billing_profiles(
            billing_settings, include_disabled=include_disabled,
        )

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

    return router
