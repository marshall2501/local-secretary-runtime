"""Compatibility alias. Use ritsuko.core.observation_loop."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("ritsuko.core.observation_loop")
_sys.modules[__name__] = _impl
