"""Compatibility alias. Use integrations.connections.service_connections."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("integrations.connections.service_connections")
_sys.modules[__name__] = _impl
