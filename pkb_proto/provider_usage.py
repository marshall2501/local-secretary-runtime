"""Read-only provider usage adapters for the daily Local Secretary GUI."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OPENAI_API_BASE = "https://api.openai.com/v1"
OPENAI_USAGE_KEY_ENVS = ("OPENAI_ADMIN_KEY", "OPENAI_API_KEY")


class ProviderUsageError(RuntimeError):
    """Safe provider usage error that never includes credential values."""


def _openai_key() -> tuple[str, str]:
    for name in OPENAI_USAGE_KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    raise ProviderUsageError(
        "OpenAI APIキーがありません。OPENAI_ADMIN_KEY または OPENAI_API_KEY を設定してください。"
    )


def _get_json(path: str, params: dict, *, api_key: str, timeout: float = 20.0) -> dict:
    query = urlencode(params, doseq=True)
    request = Request(
        f"{OPENAI_API_BASE}{path}?{query}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ProviderUsageError(
                f"OpenAI Usage APIの認証/権限が不足しています (HTTP {exc.code})。"
                "Organization Usage/Costsを読めるキーを確認してください。"
            ) from exc
        if exc.code == 429:
            raise ProviderUsageError("OpenAI Usage APIのレート制限です (HTTP 429)。") from exc
        raise ProviderUsageError(f"OpenAI Usage APIエラー (HTTP {exc.code})。") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderUsageError("OpenAI Usage APIへ接続できません。") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderUsageError("OpenAI Usage APIの応答を解析できません。") from exc


def _paged(path: str, params: dict, *, api_key: str) -> list[dict]:
    rows: list[dict] = []
    page = None
    for _ in range(100):
        request_params = dict(params)
        if page:
            request_params["page"] = page
        payload = _get_json(path, request_params, api_key=api_key)
        rows.extend(payload.get("data") or [])
        if not payload.get("has_more"):
            return rows
        page = payload.get("next_page")
        if not page:
            return rows
    raise ProviderUsageError("OpenAI Usage APIのページ数が上限を超えました。")


def _month_start_epoch(now: datetime) -> int:
    current = now.astimezone(timezone.utc)
    return int(current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())


def read_openai_month_usage(now: datetime | None = None) -> dict:
    """Return current UTC-month OpenAI completion usage and organization cost."""
    fetched = now or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    start_time = _month_start_epoch(fetched)
    key_env, api_key = _openai_key()

    usage_buckets = _paged(
        "/organization/usage/completions",
        {
            "start_time": start_time,
            "bucket_width": "1d",
            "limit": 31,
            "group_by": ["model"],
        },
        api_key=api_key,
    )
    cost_buckets = _paged(
        "/organization/costs",
        {
            "start_time": start_time,
            "bucket_width": "1d",
            "limit": 31,
        },
        api_key=api_key,
    )

    totals = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    by_model: dict[str, dict] = {}
    for bucket in usage_buckets:
        for item in bucket.get("results") or []:
            input_tokens = int(item.get("input_tokens") or 0)
            output_tokens = int(item.get("output_tokens") or 0)
            requests = int(item.get("num_model_requests") or 0)
            totals["input_tokens"] += input_tokens
            totals["output_tokens"] += output_tokens
            totals["requests"] += requests
            model = str(item.get("model") or "unknown")
            row = by_model.setdefault(
                model, {"model": model, "input_tokens": 0, "output_tokens": 0, "requests": 0}
            )
            row["input_tokens"] += input_tokens
            row["output_tokens"] += output_tokens
            row["requests"] += requests

    costs: dict[str, float] = {}
    for bucket in cost_buckets:
        for item in bucket.get("results") or []:
            amount = item.get("amount") or {}
            currency = str(amount.get("currency") or "").lower()
            if not currency:
                continue
            costs[currency] = costs.get(currency, 0.0) + float(amount.get("value") or 0.0)

    return {
        "provider": "openai",
        "period_start": datetime.fromtimestamp(start_time, timezone.utc).isoformat(),
        "fetched_at": fetched.astimezone(timezone.utc).isoformat(),
        "credential_env": key_env,
        "totals": totals,
        "costs": costs,
        "by_model": sorted(
            by_model.values(),
            key=lambda row: (row["input_tokens"] + row["output_tokens"], row["requests"]),
            reverse=True,
        ),
    }
