"""Compatibility alias. Use integrations.connections.credential_resolver."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("integrations.connections.credential_resolver")
_sys.modules[__name__] = _impl
