"""Compatibility alias. FastAPI implementation lives in interfaces.api.app."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("interfaces.api.app")
_sys.modules[__name__] = _impl
