from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from pharmacy_bot.application.source_registry import SourceRegistryService
from pharmacy_bot.domain.source_registry import (
    LegalUsageStatus,
    Source,
    SourceConfiguration,
    SourceLimits,
    SourceOperation,
    SourceRegistryConflict,
    SourceStatus,
    SourceType,
)

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


class RegistryMemory:
    def __init__(self) -> None:
        self.configuration: SourceConfiguration | None = None
        self.fingerprint = ""

    async def create_or_get(
        self,
        configuration: SourceConfiguration,
        fingerprint: str,
        *,
        now: datetime,
    ) -> Source:
        self.configuration = configuration
        self.fingerprint = fingerprint
        return Source(1, 1, configuration, fingerprint, now, now)

    async def revise(
        self,
        source_id: int,
        expected_version: int,
        configuration: SourceConfiguration,
        fingerprint: str,
        *,
        actor_internal_id: int,
        reason: str,
        now: datetime,
    ) -> Source:
        self.configuration = configuration
        self.fingerprint = fingerprint
        return Source(source_id, expected_version + 1, configuration, fingerprint, now, now)


def configuration(**changes: object) -> SourceConfiguration:
    value = SourceConfiguration(
        code=" Test-Source ",
        name=" Тестовая аптека ",
        source_type=SourceType.PARTNER_API,
        status=SourceStatus.ACTIVE,
        legal_status=LegalUsageStatus.ALLOWED,
        adapter_version="adapter-1.0",
        capability_version="2026.07",
        capabilities=frozenset(
            {
                SourceOperation.HEALTH,
                SourceOperation.SEARCH_PRODUCTS,
                SourceOperation.CHECK_AVAILABILITY,
            }
        ),
        base_urls=("https://API.Example.COM/v1/",),
        redirect_hosts=("cdn.example.com",),
        limits=SourceLimits(100, 60, 5, 300, 60),
    )
    return replace(value, **changes)


async def test_configuration_is_normalized_and_fingerprinted_deterministically() -> None:
    repository = RegistryMemory()
    service = SourceRegistryService(repository)

    created = await service.create_or_get(configuration(), now=NOW)
    equivalent = service.normalize(
        configuration(
            base_urls=("https://api.example.com/v1", "https://api.example.com/v1/"),
            capabilities=frozenset(reversed(tuple(configuration().capabilities))),
        )
    )

    assert created.configuration.code == "test-source"
    assert created.configuration.name == "Тестовая аптека"
    assert created.configuration.base_urls == ("https://api.example.com/v1",)
    assert created.configuration.redirect_hosts == ("cdn.example.com",)
    assert service.fingerprint(equivalent) == created.fingerprint
    assert set(SourceRegistryService.fingerprint(equivalent)) <= set("0123456789abcdef")


@pytest.mark.parametrize(
    ("changes", "operation", "reason"),
    [
        ({}, SourceOperation.GET_PRICE, "capability_not_declared"),
        (
            {"status": SourceStatus.DISABLED},
            SourceOperation.HEALTH,
            "source_status:disabled",
        ),
        (
            {"status": SourceStatus.DEGRADED},
            SourceOperation.HEALTH,
            "source_status:degraded",
        ),
        (
            {"legal_status": LegalUsageStatus.REVIEW_REQUIRED},
            SourceOperation.HEALTH,
            "legal_status:review_required",
        ),
        (
            {"legal_status": LegalUsageStatus.BLOCKED},
            SourceOperation.HEALTH,
            "legal_status:blocked",
        ),
    ],
)
def test_operations_fail_closed(
    changes: dict[str, object],
    operation: SourceOperation,
    reason: str,
) -> None:
    value = SourceRegistryService(RegistryMemory()).normalize(configuration(**changes))

    decision = SourceRegistryService.operation_decision(value, operation)

    assert not decision.allowed
    assert reason in decision.reasons


def test_declared_operation_is_allowed_only_for_active_legal_source() -> None:
    value = SourceRegistryService(RegistryMemory()).normalize(configuration())

    assert SourceRegistryService.operation_decision(value, SourceOperation.HEALTH).allowed


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "https://user:secret@api.example.com",
        "https://api.example.com:8443",
        "https://api.example.com?q=secret",
        "https://api.example.com#fragment",
        "https://localhost",
        "https://127.0.0.1",
        "https://bad host.example",
    ],
)
def test_invalid_base_url_is_rejected(url: str) -> None:
    with pytest.raises(SourceRegistryConflict):
        SourceRegistryService(RegistryMemory()).normalize(configuration(base_urls=(url,)))


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://api.example.com/v1/products", True),
        ("https://cdn.example.com/redirected", True),
        ("https://evilapi.example.com", False),
        ("https://sub.api.example.com", False),
        ("http://api.example.com", False),
        ("https://api.example.com:8443", False),
        ("https://user:password@api.example.com", False),
    ],
)
def test_host_allowlist_uses_exact_https_boundaries(url: str, allowed: bool) -> None:
    value = SourceRegistryService(RegistryMemory()).normalize(configuration())

    assert SourceRegistryService.host_allowed(value, url) is allowed


@pytest.mark.parametrize(
    "changes",
    [
        {"code": "x"},
        {"adapter_version": "bad version"},
        {"capabilities": frozenset()},
        {"base_urls": ()},
        {"limits": SourceLimits(0, 60, 1, 300, 60)},
        {"limits": SourceLimits(1, 60, 1, 30, 31)},
        {
            "source_type": SourceType.PUBLIC_API,
            "capabilities": frozenset({SourceOperation.RECEIVE_WEBHOOK}),
        },
        {
            "source_type": SourceType.PUBLIC_PAGE,
            "capabilities": frozenset({SourceOperation.IMPORT_EXPORT}),
        },
    ],
)
def test_invalid_identity_limits_and_capability_combinations_are_rejected(
    changes: dict[str, object],
) -> None:
    with pytest.raises(SourceRegistryConflict):
        SourceRegistryService(RegistryMemory()).normalize(configuration(**changes))


async def test_revision_requires_auditable_actor_and_reason() -> None:
    service = SourceRegistryService(RegistryMemory())

    with pytest.raises(SourceRegistryConflict):
        await service.revise(
            1,
            1,
            configuration(),
            actor_internal_id=0,
            reason=" ",
            now=NOW,
        )
