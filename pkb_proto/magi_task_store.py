"""Compatibility alias. Use ritsuko.tasks.magi_task_store."""
from importlib import import_module as _import_module
import sys as _sys
_impl = _import_module("ritsuko.tasks.magi_task_store")
_sys.modules[__name__] = _impl
