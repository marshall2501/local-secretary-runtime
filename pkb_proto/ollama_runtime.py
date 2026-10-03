"""Compatibility shim. Use integrations.llm.ollama_runtime."""
from importlib import import_module as _import_module
_impl = _import_module("integrations.llm.ollama_runtime")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
