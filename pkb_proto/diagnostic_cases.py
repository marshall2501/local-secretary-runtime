"""Compatibility alias. Use interfaces.workbench.diagnostic_cases."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("interfaces.workbench.diagnostic_cases")
if __name__ == "__main__" and hasattr(_impl, "main"):
    _impl.main()
else:
    _sys.modules[__name__] = _impl
