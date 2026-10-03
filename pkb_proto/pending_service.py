"""Compatibility alias. Use pkb.pending_service."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("pkb.pending_service")
_sys.modules[__name__] = _impl
