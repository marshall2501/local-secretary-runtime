"""Service Billing profile rules and persistence-port delegates."""
from __future__ import annotations

from typing import Protocol


class ServiceBillingSettingsRepository(Protocol):
    def list_service_billing_profiles(
        self, *, include_disabled: bool = False
    ) -> list[dict]: ...

    def upsert_service_billing_profile(self, **kwargs) -> dict: ...

    def bootstrap_openai_billing_profile(self) -> dict | None: ...


def list_service_billing_profiles(
    repository: ServiceBillingSettingsRepository,
    *,
    include_disabled: bool = False,
) -> list[dict]:
    return repository.list_service_billing_profiles(
        include_disabled=include_disabled
    )


def upsert_service_billing_profile(
    repository: ServiceBillingSettingsRepository,
    **kwargs,
) -> dict:
    return repository.upsert_service_billing_profile(**kwargs)


def bootstrap_openai_billing_profile(
    repository: ServiceBillingSettingsRepository,
) -> dict | None:
    return repository.bootstrap_openai_billing_profile()
