"""Shared Ollama runtime options.

The project treats context size as an execution setting, not a model identity.
New Ollama calls default to 64K unless an explicit profile or environment value
overrides it.
"""
from __future__ import annotations

import os

DEFAULT_OLLAMA_CONTEXT_TOKENS = 65536
OLLAMA_CONTEXT_OPTIONS = (4096, 8192, 16384, 32768, 65536, 131072)
_MIN_CONTEXT_TOKENS = 1024
_MAX_CONTEXT_TOKENS = 1048576


def normalize_context_tokens(value: object | None, *, default: int = DEFAULT_OLLAMA_CONTEXT_TOKENS) -> int:
    if value is None or str(value).strip() == "":
        value = default
    try:
        tokens = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_ollama_context_tokens") from exc
    if not _MIN_CONTEXT_TOKENS <= tokens <= _MAX_CONTEXT_TOKENS:
        raise ValueError("invalid_ollama_context_tokens")
    return tokens


def configured_context_tokens(env_name: str | None = None) -> int:
    if env_name:
        raw = os.environ.get(env_name, "").strip()
        if raw:
            return normalize_context_tokens(raw)
    raw = os.environ.get("LSA_OLLAMA_CONTEXT_TOKENS", "").strip()
    return normalize_context_tokens(raw or DEFAULT_OLLAMA_CONTEXT_TOKENS)
