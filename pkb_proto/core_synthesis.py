"""Compatibility alias. Use ritsuko.core.core_synthesis."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("ritsuko.core.core_synthesis")
_sys.modules[__name__] = _impl
