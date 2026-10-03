"""Compatibility shim. Use ritsuko.tasks.magi_task_store."""
from importlib import import_module as _import_module
_impl = _import_module("ritsuko.tasks.magi_task_store")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
