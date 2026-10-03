"""Compatibility shim. Use ritsuko.magi.async_execution."""
from importlib import import_module as _import_module
_impl = _import_module("ritsuko.magi.async_execution")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
