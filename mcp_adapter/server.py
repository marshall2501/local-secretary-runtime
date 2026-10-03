"""Compatibility entrypoint. Use interfaces.mcp.server."""
from importlib import import_module as _import_module
_impl = _import_module("interfaces.mcp.server")
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})

if __name__ == "__main__":
    mcp.run()
