"""Compatibility alias. Use integrations.llm.ollama_runtime."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("integrations.llm.ollama_runtime")
_sys.modules[__name__] = _impl
