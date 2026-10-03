"""Compatibility alias. Use ritsuko.core.magi_bridge."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("ritsuko.core.magi_bridge")
_sys.modules[__name__] = _impl
