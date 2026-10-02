"""Credential resolution for runtime-only secrets.

DB-backed Service Connections are the normal source after migration. Existing
env: references remain as bootstrap/fallback compatibility.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable

_ENV_REF = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")

_CONNECTION_LOADER: Callable[[str], str | None] | None = None


class CredentialResolutionError(RuntimeError):
    """Safe credential error that never includes the secret value."""


def register_connection_credential_loader(
    loader: Callable[[str], str | None] | None,
) -> None:
    global _CONNECTION_LOADER
    _CONNECTION_LOADER = loader


def normalize_credential_ref(value: object | None) -> str | None:
    if value is None:
        return None
    ref = str(value).strip()
    if not ref:
        return None
    if not _ENV_REF.fullmatch(ref):
        raise ValueError("unsupported_credential_ref")
    return ref


def env_name_to_credential_ref(value: object | None) -> str | None:
    if value is None:
        return None
    name = str(value).strip()
    if not name:
        return None
    if not _ENV_NAME.fullmatch(name):
        raise ValueError("invalid_credential_env")
    return "env:" + name


def credential_ref_to_env_name(value: object | None) -> str | None:
    ref = normalize_credential_ref(value)
    if ref is None:
        return None
    match = _ENV_REF.fullmatch(ref)
    return match.group(1) if match else None


def resolve_credential(value: object | None) -> str | None:
    ref = normalize_credential_ref(value)
    if ref is None:
        return None
    env_name = credential_ref_to_env_name(ref)
    secret = os.environ.get(env_name or "", "").strip()
    if not secret:
        raise CredentialResolutionError(
            "credential reference is not available: " + ref
        )
    return secret


def resolve_connection_credential(
    connection_id: object | None,
    fallback_ref: object | None = None,
) -> str | None:
    connection_text = str(connection_id or "").strip()
    if connection_text and _CONNECTION_LOADER is not None:
        try:
            secret = _CONNECTION_LOADER(connection_text)
        except Exception:
            secret = None
        if secret:
            return secret

    try:
        return resolve_credential(fallback_ref)
    except CredentialResolutionError:
        if connection_text:
            raise CredentialResolutionError(
                "connection credential is not configured: " + connection_text
            )
        raise
