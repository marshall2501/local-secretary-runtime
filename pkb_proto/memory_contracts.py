"""Compatibility alias. Use pkb.memory_contracts."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("pkb.memory_contracts")
_sys.modules[__name__] = _impl
