"""Compatibility shim. Use infrastructure.postgres.read_repository."""
from importlib import import_module as _import_module
_impl = _import_module("infrastructure.postgres.read_repository")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
