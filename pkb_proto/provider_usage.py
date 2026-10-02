"""Read-only provider usage adapters through shared Service Connections."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .credential_resolver import CredentialResolutionError, resolve_connection_credential
from .service_connections import PROVIDER_USAGE_READ


class ProviderUsageError(RuntimeError):
    """Safe provider usage error that never includes credential values."""


def _get_json(
    base_url: str,
    path: str,
    params: dict,
    *,
    api_key: str,
    timeout: float = 20.0,
) -> dict:
    query = urlencode(params, doseq=True)
    request = Request(
        base_url.rstrip("/") + path + "?" + query,
        headers={
            "Authorization": "Bearer " + api_key,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ProviderUsageError(
                f"Provider Usage APIの認証/権限が不足しています (HTTP {exc.code})。"
            ) from exc
        if exc.code == 429:
            raise ProviderUsageError(
                "Provider Usage APIのレート制限です (HTTP 429)。"
            ) from exc
        raise ProviderUsageError(
            f"Provider Usage APIエラー (HTTP {exc.code})。"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderUsageError(
            "Provider Usage APIへ接続できません。"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderUsageError(
            "Provider Usage APIの応答を解析できません。"
        ) from exc


def _paged(
    base_url: str,
    path: str,
    params: dict,
    *,
    api_key: str,
) -> list[dict]:
    rows: list[dict] = []
    page = None
    for _ in range(100):
        request_params = dict(params)
        if page:
            request_params["page"] = page
        payload = _get_json(
            base_url,
            path,
            request_params,
            api_key=api_key,
        )
        rows.extend(payload.get("data") or [])
        if not payload.get("has_more"):
            return rows
        page = payload.get("next_page")
        if not page:
            return rows
    raise ProviderUsageError(
        "Provider Usage APIのページ数が上限を超えました。"
    )


def _month_start_epoch(now: datetime) -> int:
    current = now.astimezone(timezone.utc)
    return int(
        current.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ).timestamp()
    )


def _validate_usage_connection(connection: dict) -> tuple[str, str, str | None, str]:
    if not isinstance(connection, dict):
        raise ProviderUsageError("Service Connectionが指定されていません。")
    if not connection.get("enabled"):
        raise ProviderUsageError("Service Connectionが無効です。")
    capabilities = set(connection.get("capabilities") or [])
    if PROVIDER_USAGE_READ not in capabilities:
        raise ProviderUsageError(
            "このService Connectionにはprovider_usage_read capabilityがありません。"
        )
    adapter = str(connection.get("adapter_key") or "").strip().lower()
    endpoint = str(connection.get("endpoint") or "").strip()
    credential_ref = str(connection.get("credential_ref") or "").strip() or None
    connection_id = str(connection.get("id") or "").strip()
    if not adapter or not endpoint or not connection_id:
        raise ProviderUsageError("Service Connectionの接続情報が不足しています。")
    return adapter, endpoint, credential_ref, connection_id


def read_openai_month_usage(
    connection: dict,
    now: datetime | None = None,
) -> dict:
    """Return current UTC-month OpenAI usage through one Service Connection."""
    adapter, endpoint, credential_ref, connection_id = _validate_usage_connection(connection)
    if adapter != "openai":
        raise ProviderUsageError(
            "OpenAI Usage readerにOpenAI以外のConnectionが指定されました。"
        )
    try:
        api_key = resolve_connection_credential(connection_id, credential_ref)
    except CredentialResolutionError as exc:
        raise ProviderUsageError(str(exc)) from exc
    if not api_key:
        raise ProviderUsageError("OpenAI Usage用credentialがありません。")

    fetched = now or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    start_time = _month_start_epoch(fetched)

    usage_buckets = _paged(
        endpoint,
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
        endpoint,
        "/organization/costs",
        {
            "start_time": start_time,
            "bucket_width": "1d",
            "limit": 31,
        },
        api_key=api_key,
    )

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "requests": 0,
    }
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
                model,
                {
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "requests": 0,
                },
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
            costs[currency] = (
                costs.get(currency, 0.0)
                + float(amount.get("value") or 0.0)
            )

    return {
        "connection_id": str(connection.get("id") or ""),
        "connection_name": str(connection.get("display_name") or ""),
        "provider": "openai",
        "capability": PROVIDER_USAGE_READ,
        "period_start": datetime.fromtimestamp(
            start_time,
            timezone.utc,
        ).isoformat(),
        "fetched_at": fetched.astimezone(timezone.utc).isoformat(),
        "credential_source": "service_connection",
        "usage": {
            "status": "known",
            "totals": totals,
            "by_model": sorted(
                by_model.values(),
                key=lambda row: (
                    row["input_tokens"] + row["output_tokens"],
                    row["requests"],
                ),
                reverse=True,
            ),
        },
        "costs": {
            "status": "known",
            "values": costs,
        },
        "limits": {
            "status": "unknown",
            "reason": "not_provided_by_openai_usage_adapter",
        },
        "credits": {
            "status": "unknown",
            "reason": "not_provided_by_openai_usage_adapter",
        },
        "metadata": {},
    }


def read_provider_month_usage(
    connection: dict,
    now: datetime | None = None,
) -> dict:
    adapter = str(connection.get("adapter_key") or "").strip().lower()
    if adapter == "openai":
        return read_openai_month_usage(connection, now)
    raise ProviderUsageError(
        "このService ConnectionのUsage adapterは未実装です: "
        + (adapter or "unknown")
    )
