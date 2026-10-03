"""Compatibility alias. Use capabilities.web_research.web_research."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("capabilities.web_research.web_research")
_sys.modules[__name__] = _impl
