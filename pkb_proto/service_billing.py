"""Compatibility alias. Use capabilities.service_billing.service."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("capabilities.service_billing.service")
_sys.modules[__name__] = _impl
