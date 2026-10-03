"""Compatibility shim. Use application.read_service."""
from importlib import import_module as _import_module
_impl = _import_module("application.read_service")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
