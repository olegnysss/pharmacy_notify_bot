from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from pharmacy_bot.application.adapter_contract import (
    CONTRACT_VERSION,
    AdapterContractValidator,
    AdapterIngestionService,
    adapter_request_fingerprint,
    contract_fingerprint,
    decode_envelope,
    encode_envelope,
)
from pharmacy_bot.domain.adapter_contract import (
    AdapterCallContext,
    AdapterContractError,
    AdapterDescriptor,
    AdapterEnvelope,
    AdapterErrorKind,
    AdapterIngestionReceipt,
    AdapterRequest,
    AdapterTemporaryError,
    AvailabilityQuery,
    AvailabilityResult,
    AvailabilityStatus,
    HealthQuery,
    HealthResult,
    NormalizedAvailability,
    NormalizedPharmacy,
    NormalizedProduct,
    PharmacyListQuery,
    PharmacyListResult,
    ProductCardQuery,
    ProductCardResult,
    ProductSearchQuery,
    ProductSearchResult,
    UnsupportedOperationResult,
)
from pharmacy_bot.domain.source_registry import (
    LegalUsageStatus,
    Source,
    SourceConfiguration,
    SourceLimits,
    SourceOperation,
    SourceStatus,
    SourceType,
)

NOW = datetime(2026, 7, 29, 17, tzinfo=UTC)
OPERATIONS = frozenset(
    {
        SourceOperation.HEALTH,
        SourceOperation.SEARCH_PRODUCTS,
        SourceOperation.GET_PRODUCT,
        SourceOperation.LIST_PHARMACIES,
        SourceOperation.CHECK_AVAILABILITY,
    }
)


def registered_source() -> Source:
    configuration = SourceConfiguration(
        "adapter-test",
        "Adapter test",
        SourceType.PARTNER_API,
        SourceStatus.ACTIVE,
        LegalUsageStatus.ALLOWED,
        "adapter-1",
        "capabilities-1",
        OPERATIONS,
        ("https://adapter.example/api",),
        (),
        SourceLimits(100, 60, 5, 300, 30),
    )
    return Source(7, 1, configuration, "f" * 64, NOW, NOW)


def context(
    key: str = "adapter-request-1",
    *,
    correlation: str | None = None,
) -> AdapterCallContext:
    return AdapterCallContext(
        correlation or str(uuid4()),
        str(uuid4()),
        key,
    )


def product() -> NormalizedProduct:
    return NormalizedProduct(
        "product-1",
        "Тест 10 мг",
        "https://adapter.example/api/products/1",
        None,
        "таблетки",
        "10 мг",
        20,
        None,
    )


def availability() -> NormalizedAvailability:
    return NormalizedAvailability(
        "product-1",
        None,
        AvailabilityStatus.UNKNOWN,
        None,
        None,
        None,
        NOW,
    )


def contract_cases() -> list[tuple[AdapterRequest, AdapterEnvelope]]:
    return [
        (
            AdapterRequest(SourceOperation.HEALTH, context("health"), HealthQuery()),
            AdapterEnvelope(
                "adapter-test",
                "adapter-1",
                CONTRACT_VERSION,
                "health/1",
                SourceOperation.HEALTH,
                HealthResult(True, NOW),
            ),
        ),
        (
            AdapterRequest(
                SourceOperation.SEARCH_PRODUCTS,
                context("search"),
                ProductSearchQuery("тест", None, 10),
            ),
            AdapterEnvelope(
                "adapter-test",
                "adapter-1",
                CONTRACT_VERSION,
                "product-search/1",
                SourceOperation.SEARCH_PRODUCTS,
                ProductSearchResult((product(),), None),
            ),
        ),
        (
            AdapterRequest(
                SourceOperation.GET_PRODUCT,
                context("card"),
                ProductCardQuery("product-1"),
            ),
            AdapterEnvelope(
                "adapter-test",
                "adapter-1",
                CONTRACT_VERSION,
                "product-card/1",
                SourceOperation.GET_PRODUCT,
                ProductCardResult(product()),
            ),
        ),
        (
            AdapterRequest(
                SourceOperation.LIST_PHARMACIES,
                context("pharmacies"),
                PharmacyListQuery(None, None, 10),
            ),
            AdapterEnvelope(
                "adapter-test",
                "adapter-1",
                CONTRACT_VERSION,
                "pharmacies/1",
                SourceOperation.LIST_PHARMACIES,
                PharmacyListResult(
                    (
                        NormalizedPharmacy(
                            "pharmacy-1",
                            "Аптека",
                            None,
                            Decimal("55.75"),
                            Decimal("37.61"),
                            None,
                        ),
                    ),
                    None,
                ),
            ),
        ),
        (
            AdapterRequest(
                SourceOperation.CHECK_AVAILABILITY,
                context("availability"),
                AvailabilityQuery(("product-1",), ()),
            ),
            AdapterEnvelope(
                "adapter-test",
                "adapter-1",
                CONTRACT_VERSION,
                "availability/1",
                SourceOperation.CHECK_AVAILABILITY,
                AvailabilityResult((availability(),)),
            ),
        ),
    ]


class MemoryRepository:
    def __init__(self) -> None:
        self.receipts: dict[tuple[int, str], AdapterIngestionReceipt] = {}
        self.store_calls = 0

    async def get(
        self,
        source_id: int,
        idempotency_key: str,
    ) -> AdapterIngestionReceipt | None:
        return self.receipts.get((source_id, idempotency_key))

    async def store(
        self,
        source_id: int,
        request_fingerprint: str,
        result_fingerprint: str,
        request: AdapterRequest,
        envelope: AdapterEnvelope,
        *,
        now: datetime,
    ) -> AdapterIngestionReceipt:
        self.store_calls += 1
        key = (source_id, request.context.idempotency_key)
        existing = self.receipts.get(key)
        if existing is not None:
            return existing
        receipt = AdapterIngestionReceipt(
            len(self.receipts) + 1,
            source_id,
            request.context.idempotency_key,
            request_fingerprint,
            result_fingerprint,
            request.context.correlation_id,
            request.context.causation_id,
            envelope,
            now,
        )
        self.receipts[key] = receipt
        return receipt


class FakeAdapter:
    def __init__(
        self,
        envelopes: dict[SourceOperation, AdapterEnvelope],
        *,
        operations: frozenset[SourceOperation] = OPERATIONS,
    ) -> None:
        self._envelopes = envelopes
        self._descriptor = AdapterDescriptor(
            "adapter-test",
            "adapter-1",
            frozenset({CONTRACT_VERSION}),
            operations,
        )
        self.calls = 0

    @property
    def descriptor(self) -> AdapterDescriptor:
        return self._descriptor

    async def execute(self, request: AdapterRequest) -> AdapterEnvelope:
        self.calls += 1
        return self._envelopes[request.operation]


@pytest.mark.parametrize(("request_value", "envelope"), contract_cases())
async def test_fake_adapter_passes_common_contract_suite(
    request_value: AdapterRequest,
    envelope: AdapterEnvelope,
) -> None:
    repository = MemoryRepository()
    adapter = FakeAdapter({request_value.operation: envelope})

    receipt = await AdapterIngestionService(repository).execute(
        registered_source(),
        adapter,
        request_value,
        now=NOW,
    )

    assert receipt.envelope == envelope
    assert len(receipt.request_fingerprint) == 64
    assert len(receipt.result_fingerprint) == 64
    assert decode_envelope(encode_envelope(envelope)) == envelope


async def test_retry_with_new_correlation_returns_prior_result_without_adapter_call() -> None:
    request_value, envelope = contract_cases()[1]
    repository = MemoryRepository()
    adapter = FakeAdapter({request_value.operation: envelope})
    service = AdapterIngestionService(repository)

    first = await service.execute(
        registered_source(),
        adapter,
        request_value,
        now=NOW,
    )
    retry = replace(
        request_value,
        context=context(request_value.context.idempotency_key),
    )
    repeated = await service.execute(
        registered_source(),
        adapter,
        retry,
        now=NOW,
    )

    assert repeated == first
    assert adapter.calls == 1
    assert adapter_request_fingerprint(retry) == first.request_fingerprint


async def test_reusing_idempotency_key_for_different_query_is_rejected() -> None:
    request_value, envelope = contract_cases()[1]
    repository = MemoryRepository()
    adapter = FakeAdapter({request_value.operation: envelope})
    service = AdapterIngestionService(repository)
    await service.execute(registered_source(), adapter, request_value, now=NOW)

    with pytest.raises(AdapterContractError) as captured:
        await service.execute(
            registered_source(),
            adapter,
            replace(
                request_value,
                query=ProductSearchQuery("другой товар", None, 10),
            ),
            now=NOW,
        )

    assert captured.value.kind is AdapterErrorKind.IDEMPOTENCY_CONFLICT
    assert adapter.calls == 1


async def test_unsupported_operation_is_a_typed_result() -> None:
    request_value, _ = contract_cases()[3]
    envelope = AdapterEnvelope(
        "adapter-test",
        "adapter-1",
        CONTRACT_VERSION,
        "unsupported/1",
        SourceOperation.LIST_PHARMACIES,
        UnsupportedOperationResult(
            SourceOperation.LIST_PHARMACIES,
            "not_implemented",
        ),
    )
    adapter = FakeAdapter(
        {SourceOperation.LIST_PHARMACIES: envelope},
        operations=OPERATIONS - {SourceOperation.LIST_PHARMACIES},
    )

    receipt = await AdapterIngestionService(MemoryRepository()).execute(
        registered_source(),
        adapter,
        request_value,
        now=NOW,
    )

    assert isinstance(receipt.envelope.result, UnsupportedOperationResult)


@pytest.mark.parametrize(
    "envelope_change",
    [
        {"contract_version": "pharmacy-adapter/2.0"},
        {"schema_version": "product-search/2"},
        {"source_code": "another-source"},
        {"operation": SourceOperation.GET_PRODUCT},
        {
            "result": ProductSearchResult(
                tuple(product() for _ in range(101)),
                None,
            )
        },
        {
            "result": ProductSearchResult(
                (replace(product(), name="x" * 513),),
                None,
            )
        },
    ],
)
async def test_malformed_result_does_not_reach_repository(
    envelope_change: dict[str, object],
) -> None:
    request_value, envelope = contract_cases()[1]
    malformed = replace(envelope, **envelope_change)
    repository = MemoryRepository()

    with pytest.raises(AdapterContractError):
        await AdapterIngestionService(repository).execute(
            registered_source(),
            FakeAdapter({request_value.operation: malformed}),
            request_value,
            now=NOW,
        )

    assert repository.store_calls == 0
    assert not repository.receipts


def test_missing_values_remain_explicitly_unknown_and_fingerprint_is_stable() -> None:
    request_value, envelope = contract_cases()[4]
    AdapterContractValidator().validate_envelope(
        registered_source(),
        FakeAdapter({request_value.operation: envelope}).descriptor,
        request_value,
        envelope,
    )

    restored = decode_envelope(encode_envelope(envelope))

    item = restored.result
    assert isinstance(item, AvailabilityResult)
    assert item.items[0].status is AvailabilityStatus.UNKNOWN
    assert item.items[0].quantity is None
    assert item.items[0].price is None
    assert contract_fingerprint(restored) == contract_fingerprint(envelope)


@pytest.mark.parametrize(
    "raw",
    [
        {
            **encode_envelope(contract_cases()[0][1]),
            "unknown": "must not execute",
        },
        {
            **encode_envelope(contract_cases()[0][1]),
            "payload": {
                "healthy": True,
                "observed_at": NOW.isoformat(),
                "message": None,
                "script": "dangerous",
            },
        },
    ],
)
def test_codec_rejects_unknown_fields(raw: dict[str, object]) -> None:
    with pytest.raises(AdapterContractError):
        decode_envelope(raw)


async def test_cancellation_and_typed_adapter_error_are_preserved() -> None:
    request_value, _ = contract_cases()[0]

    class FailingAdapter(FakeAdapter):
        def __init__(self, error: BaseException) -> None:
            super().__init__({})
            self.error = error

        async def execute(self, request: AdapterRequest) -> AdapterEnvelope:
            del request
            raise self.error

    with pytest.raises(asyncio.CancelledError):
        await AdapterIngestionService(MemoryRepository()).execute(
            registered_source(),
            FailingAdapter(asyncio.CancelledError()),
            request_value,
            now=NOW,
        )
    with pytest.raises(AdapterTemporaryError):
        await AdapterIngestionService(MemoryRepository()).execute(
            registered_source(),
            FailingAdapter(AdapterTemporaryError()),
            request_value,
            now=NOW,
        )
