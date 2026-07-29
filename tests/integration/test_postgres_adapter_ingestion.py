from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.adapter_contract import (
    CONTRACT_VERSION,
    AdapterIngestionService,
)
from pharmacy_bot.application.source_registry import SourceRegistryService
from pharmacy_bot.domain.adapter_contract import (
    AdapterCallContext,
    AdapterContractError,
    AdapterDescriptor,
    AdapterEnvelope,
    AdapterErrorKind,
    AdapterRequest,
    HealthQuery,
    HealthResult,
    ProductSearchQuery,
    ProductSearchResult,
)
from pharmacy_bot.domain.source_registry import (
    LegalUsageStatus,
    SourceConfiguration,
    SourceLimits,
    SourceOperation,
    SourceStatus,
    SourceType,
)
from pharmacy_bot.infrastructure.adapter_ingestion_repository import (
    SqlAlchemyAdapterIngestionRepository,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    AdapterIngestionReceiptModel,
    IntegrationRequestModel,
    SourceHealthEventModel,
    SourceHealthModel,
    SourceModel,
    SourceVersionModel,
    WebhookReceiptModel,
)
from pharmacy_bot.infrastructure.source_registry_repository import (
    SqlAlchemySourceRegistryRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


def source_configuration() -> SourceConfiguration:
    return SourceConfiguration(
        "adapter-ingestion-test",
        "Adapter ingestion test",
        SourceType.PARTNER_API,
        SourceStatus.ACTIVE,
        LegalUsageStatus.ALLOWED,
        "adapter-1",
        "capabilities-1",
        frozenset({SourceOperation.HEALTH, SourceOperation.SEARCH_PRODUCTS}),
        ("https://adapter-ingestion.example/api",),
        (),
        SourceLimits(100, 60, 5, 300, 30),
    )


def request(key: str, *, text: str = "тест") -> AdapterRequest:
    return AdapterRequest(
        SourceOperation.SEARCH_PRODUCTS,
        AdapterCallContext(str(uuid4()), str(uuid4()), key),
        ProductSearchQuery(text, None, 10),
    )


def envelope() -> AdapterEnvelope:
    return AdapterEnvelope(
        "adapter-ingestion-test",
        "adapter-1",
        CONTRACT_VERSION,
        "product-search/1",
        SourceOperation.SEARCH_PRODUCTS,
        ProductSearchResult((), None),
    )


class BarrierAdapter:
    def __init__(self, result: AdapterEnvelope) -> None:
        self._result = result
        self.calls = 0
        self._both_started = asyncio.Event()

    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            "adapter-ingestion-test",
            "adapter-1",
            frozenset({CONTRACT_VERSION}),
            frozenset({SourceOperation.HEALTH, SourceOperation.SEARCH_PRODUCTS}),
        )

    async def execute(self, request: AdapterRequest) -> AdapterEnvelope:
        del request
        self.calls += 1
        if self.calls == 2:
            self._both_started.set()
        await self._both_started.wait()
        return self._result


class NeverAdapter(BarrierAdapter):
    async def execute(self, request: AdapterRequest) -> AdapterEnvelope:
        del request
        raise AssertionError("stored retry must not call adapter")


class StaticAdapter(BarrierAdapter):
    async def execute(self, request: AdapterRequest) -> AdapterEnvelope:
        del request
        self.calls += 1
        return self._result


async def test_concurrent_ingestion_creates_one_receipt_and_retry_returns_it(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    registry = SourceRegistryService(SqlAlchemySourceRegistryRepository(session_factory))
    repository = SqlAlchemyAdapterIngestionRepository(session_factory)
    service = AdapterIngestionService(repository)
    now = datetime(2026, 7, 29, 18, tzinfo=UTC)
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(SourceHealthEventModel))
            await session.execute(delete(SourceHealthModel))
            await session.execute(delete(IntegrationRequestModel))
            await session.execute(delete(WebhookReceiptModel))
            await session.execute(delete(AdapterIngestionReceiptModel))
            await session.execute(delete(SourceVersionModel))
            await session.execute(delete(SourceModel))
        source = await registry.create_or_get(source_configuration(), now=now)
        adapter = BarrierAdapter(envelope())
        first_request = request("concurrent-key")
        second_request = request("concurrent-key")

        first, second = await asyncio.gather(
            service.execute(source, adapter, first_request, now=now),
            service.execute(source, adapter, second_request, now=now),
        )

        assert first.id == second.id
        assert adapter.calls == 2
        retry = await service.execute(
            source,
            NeverAdapter(envelope()),
            request("concurrent-key"),
            now=now,
        )
        assert retry.id == first.id

        with pytest.raises(AdapterContractError) as conflict:
            await service.execute(
                source,
                NeverAdapter(envelope()),
                request("concurrent-key", text="другой"),
                now=now,
            )
        assert conflict.value.kind is AdapterErrorKind.IDEMPOTENCY_CONFLICT

        invalid_request = AdapterRequest(
            SourceOperation.HEALTH,
            AdapterCallContext(str(uuid4()), None, "invalid-result-key"),
            HealthQuery(),
        )
        malformed = AdapterEnvelope(
            "adapter-ingestion-test",
            "adapter-1",
            CONTRACT_VERSION,
            "health/1",
            SourceOperation.HEALTH,
            HealthResult(True, now, "x" * 513),
        )
        with pytest.raises(AdapterContractError):
            await service.execute(
                source,
                StaticAdapter(malformed),
                invalid_request,
                now=now,
            )

        async with session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(AdapterIngestionReceiptModel)
            )
            model = await session.scalar(select(AdapterIngestionReceiptModel))

        assert count == 1
        assert model is not None
        assert model.source_code == "adapter-ingestion-test"
        assert model.adapter_version == "adapter-1"
        assert model.contract_version == CONTRACT_VERSION
        assert model.schema_version == "product-search/1"
        assert model.correlation_id == first.correlation_id
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(SourceHealthEventModel))
            await session.execute(delete(SourceHealthModel))
            await session.execute(delete(IntegrationRequestModel))
            await session.execute(delete(WebhookReceiptModel))
            await session.execute(delete(AdapterIngestionReceiptModel))
            await session.execute(delete(SourceVersionModel))
            await session.execute(delete(SourceModel))
        await engine.dispose()
