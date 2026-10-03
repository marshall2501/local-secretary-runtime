"""Compatibility alias. Use pkb.daily_interpreter."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("pkb.daily_interpreter")
_sys.modules[__name__] = _impl
