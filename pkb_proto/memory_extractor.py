"""Compatibility alias. Use pkb.memory_extractor."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("pkb.memory_extractor")
_sys.modules[__name__] = _impl
