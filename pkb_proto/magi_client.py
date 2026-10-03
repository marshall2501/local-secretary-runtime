"""Compatibility shim. Use ritsuko.magi.client."""
from importlib import import_module as _import_module
_impl = _import_module("ritsuko.magi.client")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
