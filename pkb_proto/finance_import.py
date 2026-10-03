"""Compatibility alias. Use capabilities.finance.finance_import."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("capabilities.finance.finance_import")
_sys.modules[__name__] = _impl
