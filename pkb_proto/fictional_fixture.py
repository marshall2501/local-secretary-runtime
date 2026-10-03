"""Compatibility alias. Use interfaces.workbench.fictional_fixture."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("interfaces.workbench.fictional_fixture")
if __name__ == "__main__" and hasattr(_impl, "main"):
    _impl.main()
else:
    _sys.modules[__name__] = _impl
