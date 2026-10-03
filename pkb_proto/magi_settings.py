"""Compatibility alias. Use ritsuko.magi.settings."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("ritsuko.magi.settings")
_sys.modules[__name__] = _impl
