"""Read-only Service Billing adapters through shared Service Connections.

Provider-specific activity, charges, subscription, credits and limits are
normalized into one snapshot. Unsupported or unproven values remain explicit
unknown states; they are never fabricated as zero.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from integrations.connections.credential_resolver import CredentialResolutionError, resolve_connection_credential
from integrations.connections.service_connections import SERVICE_BILLING_READ


class ServiceBillingError(RuntimeError):
    """Safe Service Billing error that never includes credential values."""


def _get_json(base_url: str, path: str, params: dict, *, api_key: str, timeout: float = 20.0) -> dict:
    query = urlencode(params, doseq=True)
    request = Request(
        base_url.rstrip("/") + path + "?" + query,
        headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ServiceBillingError(
                f"Service Billing APIの認証/権限が不足しています (HTTP {exc.code})。"
            ) from exc
        if exc.code == 429:
            raise ServiceBillingError("Service Billing APIのレート制限です (HTTP 429)。") from exc
        raise ServiceBillingError(f"Service Billing APIエラー (HTTP {exc.code})。") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ServiceBillingError("Service Billing APIへ接続できません。") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceBillingError("Service Billing APIの応答を解析できません。") from exc


def _paged(base_url: str, path: str, params: dict, *, api_key: str) -> list[dict]:
    rows: list[dict] = []
    page = None
    for _ in range(100):
        request_params = dict(params)
        if page:
            request_params["page"] = page
        payload = _get_json(base_url, path, request_params, api_key=api_key)
        rows.extend(payload.get("data") or [])
        if not payload.get("has_more"):
            return rows
        page = payload.get("next_page")
        if not page:
            return rows
    raise ServiceBillingError("Service Billing APIのページ数が上限を超えました。")


def _month_start(now: datetime) -> datetime:
    current = now.astimezone(timezone.utc)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _month_start_epoch(now: datetime) -> int:
    return int(_month_start(now).timestamp())


def _validate_billing_connection(connection: dict) -> tuple[str, str, str | None, str]:
    if not isinstance(connection, dict):
        raise ServiceBillingError("Service Connectionが指定されていません。")
    if not connection.get("enabled"):
        raise ServiceBillingError("Service Connectionが無効です。")
    if SERVICE_BILLING_READ not in set(connection.get("capabilities") or []):
        raise ServiceBillingError(
            "このService Connectionにはservice_billing_read capabilityがありません。"
        )
    adapter = str(connection.get("adapter_key") or "").strip().lower()
    endpoint = str(connection.get("endpoint") or "").strip()
    credential_ref = str(connection.get("credential_ref") or "").strip() or None
    connection_id = str(connection.get("id") or "").strip()
    if not adapter or not endpoint or not connection_id:
        raise ServiceBillingError("Service Connectionの接続情報が不足しています。")
    return adapter, endpoint, credential_ref, connection_id


def _unknown(reason: str) -> dict:
    return {"status": "unknown", "reason": reason}


def read_openai_month_billing(connection: dict, now: datetime | None = None) -> dict:
    adapter, endpoint, credential_ref, connection_id = _validate_billing_connection(connection)
    if adapter != "openai":
        raise ServiceBillingError("OpenAI Service Billing readerにOpenAI以外のConnectionが指定されました。")
    try:
        api_key = resolve_connection_credential(connection_id, credential_ref)
    except CredentialResolutionError as exc:
        raise ServiceBillingError(str(exc)) from exc
    if not api_key:
        raise ServiceBillingError("OpenAI Billing用credentialがありません。")

    fetched = now or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    start_time = _month_start_epoch(fetched)

    activity_buckets = _paged(
        endpoint,
        "/organization/usage/completions",
        {"start_time": start_time, "bucket_width": "1d", "limit": 31, "group_by": ["model"]},
        api_key=api_key,
    )
    charge_buckets = _paged(
        endpoint,
        "/organization/costs",
        {"start_time": start_time, "bucket_width": "1d", "limit": 31},
        api_key=api_key,
    )

    totals = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    by_model: dict[str, dict] = {}
    for bucket in activity_buckets:
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
                {"model": model, "input_tokens": 0, "output_tokens": 0, "requests": 0},
            )
            row["input_tokens"] += input_tokens
            row["output_tokens"] += output_tokens
            row["requests"] += requests

    charges: dict[str, float] = {}
    for bucket in charge_buckets:
        for item in bucket.get("results") or []:
            amount = item.get("amount") or {}
            currency = str(amount.get("currency") or "").lower()
            if not currency:
                continue
            charges[currency] = charges.get(currency, 0.0) + float(amount.get("value") or 0.0)

    return {
        "connection_id": str(connection.get("id") or ""),
        "connection_name": str(connection.get("display_name") or ""),
        "service": "openai",
        "capability": SERVICE_BILLING_READ,
        "period_start": datetime.fromtimestamp(start_time, timezone.utc).isoformat(),
        "fetched_at": fetched.astimezone(timezone.utc).isoformat(),
        "credential_source": "service_connection",
        "activity": {
            "status": "known",
            "totals": totals,
            "by_model": sorted(
                by_model.values(),
                key=lambda row: (row["input_tokens"] + row["output_tokens"], row["requests"]),
                reverse=True,
            ),
        },
        "charges": {"status": "known", "values": charges},
        "subscription": _unknown("not_provided_by_openai_billing_adapter"),
        "limits": _unknown("not_provided_by_openai_billing_adapter"),
        "credits": _unknown("not_provided_by_openai_billing_adapter"),
        "metadata": {},
    }


def _google_authorized_session(connection: dict):
    try:
        import google.auth
        from google.auth import impersonated_credentials
        from google.auth.transport.requests import AuthorizedSession
    except ImportError as exc:
        raise ServiceBillingError("Google Cloud Service Billing用dependencyが不足しています。") from exc

    config = dict(connection.get("config_data") or connection.get("nonsecret_config") or {})
    if str(config.get("credential_provider") or "").strip() != "google_adc":
        raise ServiceBillingError("Google Cloud credential_providerはgoogle_adcが必要です。")
    target = str(config.get("target_principal") or "").strip()
    if not target:
        raise ServiceBillingError("Google Cloud target_principalが未設定です。")
    try:
        source_credentials, _ = google.auth.default()
        target_credentials = impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=target,
            target_scopes=["https://www.googleapis.com/auth/monitoring.read"],
            lifetime=600,
        )
        return AuthorizedSession(target_credentials)
    except Exception as exc:
        raise ServiceBillingError(
            "Google ADCからService Account impersonation資格情報を取得できません。"
        ) from exc


def _google_time_series(
    session,
    *,
    endpoint: str,
    project_id: str,
    metric_type: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    url = endpoint.rstrip("/") + "/projects/" + quote(project_id, safe="") + "/timeSeries"
    rows: list[dict] = []
    page_token: str | None = None
    for _ in range(100):
        params = {
            "filter": f'metric.type = "{metric_type}"',
            "interval.startTime": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "interval.endTime": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "view": "FULL",
            "pageSize": 1000,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            response = session.get(url, params=params, timeout=30)
        except Exception as exc:
            raise ServiceBillingError("Google Cloud Monitoringへ接続できません。") from exc
        if not response.ok:
            status = int(getattr(response, "status_code", 0) or 0)
            try:
                body = response.json()
                message = str((body.get("error") or {}).get("message") or "").strip()
            except Exception:
                message = ""
            suffix = f" (HTTP {status})" if status else ""
            if message:
                suffix += ": " + message[:240]
            raise ServiceBillingError("Google Cloud Monitoring APIエラー" + suffix + "。")
        try:
            payload = response.json()
        except Exception as exc:
            raise ServiceBillingError("Google Cloud Monitoringの応答を解析できません。") from exc
        rows.extend(payload.get("timeSeries") or [])
        page_token = str(payload.get("nextPageToken") or "").strip() or None
        if not page_token:
            return rows
    raise ServiceBillingError("Google Cloud Monitoringのページ数が上限を超えました。")


def _series_value_total(series: dict) -> int | float:
    total: int | float = 0
    for point in series.get("points") or []:
        value = point.get("value") or {}
        if "int64Value" in value:
            total += int(value["int64Value"])
        elif "doubleValue" in value:
            total += float(value["doubleValue"])
    return total


def _quota_limit_name(series: dict) -> str:
    return str(((series.get("metric") or {}).get("labels") or {}).get("limit_name") or "")


def _quota_cadence(limit_name: str) -> str | None:
    compact = "".join(ch for ch in limit_name.lower() if ch.isalnum())
    if "perminute" in compact:
        return "minute"
    if "perday" in compact:
        return "day"
    return None


def _select_quota_series(
    series_list: list[dict],
    *,
    canonical_limit_name: str | None = None,
    preferred_cadence: str | None = None,
) -> list[dict]:
    """Choose one quota accounting axis per model without double-counting usage.

    Google evaluates the same request/token usage against multiple quota windows
    (for example per-minute and per-day). Those are enforcement axes, not
    additive usage. Monthly accounting therefore selects one deterministic axis.
    """
    if preferred_cadence not in {None, "minute", "day"}:
        raise ValueError("preferred_cadence must be minute, day, or None")

    by_model: dict[str, list[dict]] = {}
    for series in series_list:
        labels = dict((series.get("metric") or {}).get("labels") or {})
        model = str(labels.get("model") or "unknown")
        by_model.setdefault(model, []).append(series)

    selected: list[dict] = []
    for model, rows in by_model.items():
        if canonical_limit_name:
            matches = [row for row in rows if _quota_limit_name(row) == canonical_limit_name]
            if matches:
                selected.extend(matches)
                continue

        non_user = [row for row in rows if "peruser" not in _quota_limit_name(row).lower()]
        candidates = non_user or rows
        if preferred_cadence:
            cadence_matches = [
                row for row in candidates
                if _quota_cadence(_quota_limit_name(row)) == preferred_cadence
            ]
            if cadence_matches:
                candidates = cadence_matches

        limit_names = {_quota_limit_name(row) for row in candidates}
        if len(limit_names) > 1:
            raise ServiceBillingError(
                f"Google Cloud quota seriesの集計軸を一意に決められません: {model}"
            )
        selected.extend(candidates)
    return selected


def _quota_selection_debug(source: list[dict], selected: list[dict]) -> dict:
    """Return non-secret observability for the chosen accounting axis."""
    return {
        "source_series_count": len(source),
        "selected_series_count": len(selected),
        "available_limit_names": sorted({_quota_limit_name(row) for row in source}),
        "selected_limit_names": sorted({_quota_limit_name(row) for row in selected}),
    }


def _sum_series_by_model(series_list: list[dict]) -> tuple[int | float, dict[str, int | float]]:
    total: int | float = 0
    by_model: dict[str, int | float] = {}
    for series in series_list:
        labels = dict((series.get("metric") or {}).get("labels") or {})
        model = str(labels.get("model") or "unknown")
        subtotal = _series_value_total(series)
        total += subtotal
        by_model[model] = by_model.get(model, 0) + subtotal
    return total, by_model


def _latest_gauge_rows(series_list: list[dict], *, canonical_limit_name: str | None = None) -> list[dict]:
    rows: list[dict] = []
    for series in _select_quota_series(series_list, canonical_limit_name=canonical_limit_name):
        labels = dict((series.get("metric") or {}).get("labels") or {})
        points = series.get("points") or []
        if not points:
            continue
        value = points[0].get("value") or {}
        number = (
            int(value["int64Value"]) if "int64Value" in value
            else float(value["doubleValue"]) if "doubleValue" in value
            else None
        )
        if number is not None:
            rows.append({
                "model": str(labels.get("model") or "unknown"),
                "limit_name": str(labels.get("limit_name") or ""),
                "value": number,
            })
    return rows


def read_google_cloud_month_billing(connection: dict, now: datetime | None = None) -> dict:
    adapter, endpoint, _credential_ref, _connection_id = _validate_billing_connection(connection)
    if adapter != "google_cloud":
        raise ServiceBillingError(
            "Google Cloud Service Billing readerにgoogle_cloud以外のConnectionが指定されました。"
        )
    config = dict(connection.get("config_data") or connection.get("nonsecret_config") or {})
    project_id = str(config.get("project_id") or "").strip()
    target_principal = str(config.get("target_principal") or "").strip()
    if not project_id or not target_principal:
        raise ServiceBillingError("Google Cloudのproject_id / target_principalが未設定です。")

    fetched = now or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    start = _month_start(fetched)
    session = _google_authorized_session(connection)

    prefix = "generativelanguage.googleapis.com/"
    metric_types = {
        "paid_input": prefix + "quota/generate_content_paid_tier_input_token_count/usage",
        "free_input": prefix + "quota/generate_content_free_tier_input_token_count/usage",
        "paid_requests": prefix + "quota/generate_requests_per_model/usage",
        "free_requests": prefix + "quota/generate_content_free_tier_requests/usage",
        "output": prefix + "generate_content_usage_output_token_count",
        "paid_input_limit": prefix + "quota/generate_content_paid_tier_input_token_count/limit",
        "paid_request_limit": prefix + "quota/generate_requests_per_model/limit",
    }
    data = {
        name: _google_time_series(
            session,
            endpoint=endpoint,
            project_id=project_id,
            metric_type=metric_type,
            start=start,
            end=fetched,
        )
        for name, metric_type in metric_types.items()
    }

    paid_input = _select_quota_series(
        data["paid_input"],
        canonical_limit_name="GenerateContentPaidTierInputTokensPerModelPerMinute",
    )
    free_input = _select_quota_series(data["free_input"], preferred_cadence="minute")
    paid_requests = _select_quota_series(
        data["paid_requests"],
        canonical_limit_name="GenerateRequestsPerMinutePerProjectPerModel",
    )
    free_requests = _select_quota_series(data["free_requests"], preferred_cadence="minute")

    paid_input_total, paid_input_models = _sum_series_by_model(paid_input)
    free_input_total, free_input_models = _sum_series_by_model(free_input)
    paid_request_total, paid_request_models = _sum_series_by_model(paid_requests)
    free_request_total, free_request_models = _sum_series_by_model(free_requests)
    output_total, output_models = _sum_series_by_model(data["output"])

    models = (
        set(paid_input_models) | set(free_input_models)
        | set(paid_request_models) | set(free_request_models)
        | set(output_models)
    )
    by_model = [
        {
            "model": model,
            "input_tokens": int(paid_input_models.get(model, 0) + free_input_models.get(model, 0)),
            "output_tokens": int(output_models.get(model, 0)),
            "requests": int(paid_request_models.get(model, 0) + free_request_models.get(model, 0)),
        }
        for model in models
    ]
    by_model.sort(
        key=lambda row: (row["input_tokens"] + row["output_tokens"], row["requests"]),
        reverse=True,
    )

    limits_rows = (
        _latest_gauge_rows(
            data["paid_input_limit"],
            canonical_limit_name="GenerateContentPaidTierInputTokensPerModelPerMinute",
        )
        + _latest_gauge_rows(
            data["paid_request_limit"],
            canonical_limit_name="GenerateRequestsPerMinutePerProjectPerModel",
        )
    )

    return {
        "connection_id": str(connection.get("id") or ""),
        "connection_name": str(connection.get("display_name") or ""),
        "service": "google_cloud",
        "capability": SERVICE_BILLING_READ,
        "period_start": start.isoformat(),
        "fetched_at": fetched.astimezone(timezone.utc).isoformat(),
        "credential_source": "google_adc_service_account_impersonation",
        "activity": {
            "status": "known",
            "totals": {
                "input_tokens": int(paid_input_total + free_input_total),
                "output_tokens": int(output_total),
                "requests": int(paid_request_total + free_request_total),
            },
            "by_model": by_model,
            "breakdown": {
                "paid_input_tokens": int(paid_input_total),
                "free_input_tokens": int(free_input_total),
                "paid_requests": int(paid_request_total),
                "free_requests": int(free_request_total),
            },
        },
        "charges": _unknown("google_cloud_cost_reader_not_implemented"),
        "subscription": _unknown("google_ai_subscription_tier_not_exposed_by_monitoring_adapter"),
        "limits": (
            {"status": "known", "values": limits_rows}
            if limits_rows else _unknown("google_cloud_limit_series_not_available")
        ),
        "credits": _unknown("google_ai_prepaid_credit_reader_not_implemented"),
        "metadata": {
            "project_id": project_id,
            "credential_provider": "google_adc",
            "monitoring_metric_family": "generativelanguage.googleapis.com",
            "quota_accounting": {
                "policy": "one_axis_per_model_prefer_minute_for_free_tier",
                "paid_input": _quota_selection_debug(data["paid_input"], paid_input),
                "free_input": _quota_selection_debug(data["free_input"], free_input),
                "paid_requests": _quota_selection_debug(data["paid_requests"], paid_requests),
                "free_requests": _quota_selection_debug(data["free_requests"], free_requests),
            },
        },
    }


def read_service_billing_snapshot(connection: dict, now: datetime | None = None) -> dict:
    adapter = str(connection.get("adapter_key") or "").strip().lower()
    if adapter == "openai":
        return read_openai_month_billing(connection, now)
    if adapter == "google_cloud":
        return read_google_cloud_month_billing(connection, now)
    raise ServiceBillingError(
        "このService ConnectionのService Billing adapterは未実装です: "
        + (adapter or "unknown")
    )
