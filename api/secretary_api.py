"""Compatibility module. The FastAPI implementation lives in interfaces.api.app."""
from importlib import import_module as _import_module
_impl = _import_module("interfaces.api.app")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
