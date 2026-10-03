"""Compatibility alias. Use ritsuko.magi.protocol."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("ritsuko.magi.protocol")
_sys.modules[__name__] = _impl
