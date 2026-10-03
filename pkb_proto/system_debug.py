"""Compatibility alias. Use infrastructure.system_debug."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("infrastructure.system_debug")
_sys.modules[__name__] = _impl
