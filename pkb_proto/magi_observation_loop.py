"""Compatibility shim. Use ritsuko.core.observation_loop."""
from importlib import import_module as _import_module
_impl = _import_module("ritsuko.core.observation_loop")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
