"""Resolve credential references without storing or returning secret values."""
from __future__ import annotations

import os
import re

_ENV_REF = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")


class CredentialResolutionError(RuntimeError):
    """Credential lookup failed without exposing the secret value."""


def normalize_credential_ref(value: str | None) -> str | None:
    if value is None:
        return None
    ref = str(value).strip()
    if not ref:
        return None
    match = _ENV_REF.fullmatch(ref)
    if not match:
        raise ValueError("unsupported_credential_ref")
    return "env:" + match.group(1)


def env_name_to_credential_ref(name: str | None) -> str | None:
    raw = str(name or "").strip()
    if not raw:
        return None
    return normalize_credential_ref("env:" + raw)


def credential_ref_to_env_name(ref: str | None) -> str | None:
    normalized = normalize_credential_ref(ref)
    if normalized is None:
        return None
    return normalized[4:]


def resolve_credential(ref: str | None) -> str | None:
    normalized = normalize_credential_ref(ref)
    if normalized is None:
        return None
    name = normalized[4:]
    value = os.environ.get(name, "").strip()
    if not value:
        raise CredentialResolutionError(
            "Configured credential reference is unavailable: " + normalized
        )
    return value
