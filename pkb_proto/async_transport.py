"""Compatibility alias. Use infrastructure.async_runtime.transport."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("infrastructure.async_runtime.transport")
_sys.modules[__name__] = _impl
