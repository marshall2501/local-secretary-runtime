"""Compatibility shim. Use infrastructure.async_runtime.transport."""
from importlib import import_module as _import_module
_impl = _import_module("infrastructure.async_runtime.transport")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
