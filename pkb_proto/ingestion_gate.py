"""Compatibility alias. Use pkb.ingestion_gate."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("pkb.ingestion_gate")
_sys.modules[__name__] = _impl
