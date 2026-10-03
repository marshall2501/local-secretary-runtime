"""Compatibility shim. Use ritsuko.core.magi_bridge."""
from importlib import import_module as _import_module
_impl = _import_module("ritsuko.core.magi_bridge")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
