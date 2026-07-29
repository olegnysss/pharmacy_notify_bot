from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from pharmacy_bot.application.source_integration import (
    HmacSha256WebhookAuthenticator,
    IntegrationObservabilityService,
    SourceResponseCache,
    WebhookIntakeService,
    evolve_source_health,
    source_cache_fingerprint,
)
from pharmacy_bot.domain.source_integration import (
    CacheLookupStatus,
    IntegrationOutcome,
    IntegrationRequestInput,
    SourceCacheKey,
    SourceCacheRecord,
    SourceHealth,
    SourceHealthPolicy,
    SourceHealthStatus,
    WebhookPayloadRejected,
    WebhookPolicy,
    WebhookReceipt,
    WebhookReceiptStatus,
    WebhookReceiveOutcome,
    WebhookRejected,
    WebhookRejectionKind,
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
from pharmacy_bot.infrastructure.source_cache import InMemorySourceCacheRepository

NOW = datetime(2026, 7, 29, 18, 30, tzinfo=UTC)
SECRET = b"a sufficiently long webhook secret"


def webhook_source() -> Source:
    configuration = SourceConfiguration(
        "webhook-source",
        "Webhook source",
        SourceType.WEBHOOK,
        SourceStatus.ACTIVE,
        LegalUsageStatus.ALLOWED,
        "adapter-1",
        "capabilities-1",
        frozenset({SourceOperation.RECEIVE_WEBHOOK}),
        ("https://webhook.example/api",),
        (),
        SourceLimits(100, 60, 5, 300, 30),
    )
    return Source(11, 1, configuration, "f" * 64, NOW, NOW)


class MemoryWebhookRepository:
    def __init__(self) -> None:
        self.receipts: dict[tuple[int, str], WebhookReceipt] = {}
        self.lock = asyncio.Lock()
        self.claim_calls = 0

    async def claim(
        self,
        source_id: int,
        delivery_key: str,
        body_digest: str,
        event_timestamp: datetime,
        body_bytes: int,
        *,
        received_at: datetime,
    ) -> tuple[WebhookReceipt, bool]:
        async with self.lock:
            self.claim_calls += 1
            key = (source_id, delivery_key)
            existing = self.receipts.get(key)
            if existing is not None:
                if existing.body_digest != body_digest:
                    raise WebhookRejected(
                        WebhookRejectionKind.REPLAY_CONFLICT,
                        "conflict",
                    )
                return existing, False
            receipt = WebhookReceipt(
                len(self.receipts) + 1,
                source_id,
                delivery_key,
                body_digest,
                event_timestamp,
                body_bytes,
                WebhookReceiptStatus.PROCESSING,
                None,
                None,
                received_at,
                None,
            )
            self.receipts[key] = receipt
            return receipt, True

    async def accept(
        self,
        receipt_id: int,
        business_fingerprint: str,
        *,
        completed_at: datetime,
    ) -> WebhookReceipt:
        return self._complete(
            receipt_id,
            WebhookReceiptStatus.ACCEPTED,
            business_fingerprint,
            None,
            completed_at,
        )

    async def quarantine(
        self,
        receipt_id: int,
        reason_code: str,
        *,
        completed_at: datetime,
    ) -> WebhookReceipt:
        return self._complete(
            receipt_id,
            WebhookReceiptStatus.QUARANTINED,
            None,
            reason_code,
            completed_at,
        )

    def _complete(
        self,
        receipt_id: int,
        status: WebhookReceiptStatus,
        fingerprint: str | None,
        reason: str | None,
        completed_at: datetime,
    ) -> WebhookReceipt:
        key, receipt = next(
            (key, value) for key, value in self.receipts.items() if value.id == receipt_id
        )
        completed = replace(
            receipt,
            status=status,
            business_fingerprint=fingerprint,
            quarantine_reason=reason,
            completed_at=completed_at,
        )
        self.receipts[key] = completed
        return completed


class Processor:
    def __init__(self, *, rejection: str | None = None) -> None:
        self.calls = 0
        self.rejection = rejection

    async def process(self, body: bytes, receipt: WebhookReceipt) -> str:
        del receipt
        self.calls += 1
        await asyncio.sleep(0)
        if self.rejection:
            raise WebhookPayloadRejected(self.rejection)
        return sha256(b"business:" + body).hexdigest()


def webhook_arguments(
    body: bytes = b'{"event":"stock"}',
    *,
    key: str = "delivery-1",
    timestamp: datetime = NOW,
) -> dict[str, object]:
    raw_timestamp = str(int(timestamp.timestamp()))
    return {
        "secret": SECRET,
        "signature": HmacSha256WebhookAuthenticator.sign(
            SECRET,
            raw_timestamp,
            body,
        ),
        "timestamp": raw_timestamp,
        "delivery_key": key,
        "content_type": "application/json; charset=utf-8",
        "body": body,
        "now": NOW,
    }


async def test_authenticated_webhook_is_accepted_without_persisting_raw_body() -> None:
    repository = MemoryWebhookRepository()
    processor = Processor()

    result = await WebhookIntakeService(repository).receive(
        webhook_source(),
        WebhookPolicy(1_000, 300, 30),
        processor,
        **webhook_arguments(),  # type: ignore[arg-type]
    )

    assert result.outcome is WebhookReceiveOutcome.ACCEPTED
    assert result.receipt.status is WebhookReceiptStatus.ACCEPTED
    assert result.receipt.business_fingerprint
    assert processor.calls == 1
    persisted = repr(result.receipt)
    assert "stock" not in persisted
    assert SECRET.decode() not in persisted


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        ({"signature": "sha256=" + "0" * 64}, WebhookRejectionKind.INVALID_SIGNATURE),
        (
            {"timestamp": str(int((NOW - timedelta(minutes=10)).timestamp()))},
            WebhookRejectionKind.INVALID_TIMESTAMP,
        ),
        ({"content_type": "text/plain"}, WebhookRejectionKind.INVALID_CONTENT_TYPE),
        ({"delivery_key": "bad key"}, WebhookRejectionKind.INVALID_DELIVERY_KEY),
        ({"body": b"x" * 1_001}, WebhookRejectionKind.OVERSIZED),
    ],
)
async def test_untrusted_webhook_is_rejected_before_business_parse(
    changes: dict[str, object],
    kind: WebhookRejectionKind,
) -> None:
    repository = MemoryWebhookRepository()
    processor = Processor()
    arguments = webhook_arguments()
    arguments.update(changes)

    with pytest.raises(WebhookRejected) as captured:
        await WebhookIntakeService(repository).receive(
            webhook_source(),
            WebhookPolicy(1_000, 300, 30),
            processor,
            **arguments,  # type: ignore[arg-type]
        )

    assert captured.value.kind is kind
    assert processor.calls == 0
    assert repository.claim_calls == 0


async def test_concurrent_duplicate_creates_one_business_fact() -> None:
    repository = MemoryWebhookRepository()
    processor = Processor()
    service = WebhookIntakeService(repository)

    first, second = await asyncio.gather(
        service.receive(
            webhook_source(),
            WebhookPolicy(1_000, 300, 30),
            processor,
            **webhook_arguments(),  # type: ignore[arg-type]
        ),
        service.receive(
            webhook_source(),
            WebhookPolicy(1_000, 300, 30),
            processor,
            **webhook_arguments(),  # type: ignore[arg-type]
        ),
    )

    assert {first.outcome, second.outcome} == {
        WebhookReceiveOutcome.ACCEPTED,
        WebhookReceiveOutcome.DUPLICATE,
    }
    assert processor.calls == 1
    assert len(repository.receipts) == 1


async def test_delivery_key_reuse_with_another_body_is_replay_conflict() -> None:
    repository = MemoryWebhookRepository()
    service = WebhookIntakeService(repository)
    await service.receive(
        webhook_source(),
        WebhookPolicy(1_000, 300, 30),
        Processor(),
        **webhook_arguments(),  # type: ignore[arg-type]
    )

    with pytest.raises(WebhookRejected) as captured:
        await service.receive(
            webhook_source(),
            WebhookPolicy(1_000, 300, 30),
            Processor(),
            **webhook_arguments(b'{"event":"different"}'),  # type: ignore[arg-type]
        )

    assert captured.value.kind is WebhookRejectionKind.REPLAY_CONFLICT


async def test_authenticated_invalid_contract_is_quarantined_with_safe_reason() -> None:
    result = await WebhookIntakeService(MemoryWebhookRepository()).receive(
        webhook_source(),
        WebhookPolicy(1_000, 300, 30),
        Processor(rejection="invalid_schema"),
        **webhook_arguments(),  # type: ignore[arg-type]
    )

    assert result.outcome is WebhookReceiveOutcome.QUARANTINED
    assert result.receipt.quarantine_reason == "invalid_schema"


async def test_cancelled_processor_propagates_and_does_not_leave_processing_receipt() -> None:
    repository = MemoryWebhookRepository()

    class CancelledProcessor:
        async def process(self, body: bytes, receipt: WebhookReceipt) -> str:
            del body, receipt
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await WebhookIntakeService(repository).receive(
            webhook_source(),
            WebhookPolicy(1_000, 300, 30),
            CancelledProcessor(),
            **webhook_arguments(),  # type: ignore[arg-type]
        )

    receipt = next(iter(repository.receipts.values()))
    assert receipt.status is WebhookReceiptStatus.QUARANTINED
    assert receipt.quarantine_reason == "processing_cancelled"


def cache_key(**changes: object) -> SourceCacheKey:
    value = SourceCacheKey(
        "source-a",
        SourceOperation.CHECK_AVAILABILITY,
        "region-moscow",
        "user-42",
        "availability-1",
        "adapter-1",
    )
    return replace(value, **changes)


async def test_cache_namespace_separates_source_region_user_and_schema() -> None:
    repository = InMemorySourceCacheRepository()
    cache = SourceResponseCache(repository)
    original = cache_key()
    await cache.put(original, b"original", ttl_seconds=60, now=NOW)
    variants = (
        cache_key(source_code="source-b"),
        cache_key(region_key="region-spb"),
        cache_key(user_scope_key="user-43"),
        cache_key(schema_version="availability-2"),
        cache_key(adapter_version="adapter-2"),
    )

    fresh = await cache.get(original, now=NOW + timedelta(seconds=30))
    misses = [await cache.get(key, now=NOW) for key in variants]

    assert fresh.status is CacheLookupStatus.FRESH
    assert fresh.payload == b"original"
    assert all(item.status is CacheLookupStatus.MISS for item in misses)
    assert (
        len({source_cache_fingerprint(original), *(source_cache_fingerprint(v) for v in variants)})
        == 6
    )


async def test_stale_or_corrupted_cache_is_never_returned_as_fresh() -> None:
    repository = InMemorySourceCacheRepository()
    cache = SourceResponseCache(repository)
    key = cache_key()
    await cache.put(key, b"value", ttl_seconds=10, now=NOW)

    stale = await cache.get(key, now=NOW + timedelta(seconds=10))
    assert stale.status is CacheLookupStatus.STALE
    assert stale.payload is None

    fingerprint = source_cache_fingerprint(key)
    await repository.put(
        fingerprint,
        SourceCacheRecord(
            fingerprint,
            key.source_code,
            key.adapter_version,
            b"tampered",
            "0" * 64,
            NOW,
            NOW + timedelta(seconds=10),
        ),
    )
    corrupted = await cache.get(key, now=NOW)
    assert corrupted.status is CacheLookupStatus.MISS
    assert corrupted.payload is None


async def test_adapter_version_invalidation_is_source_scoped() -> None:
    repository = InMemorySourceCacheRepository()
    cache = SourceResponseCache(repository)
    old = cache_key(adapter_version="adapter-1")
    active = cache_key(adapter_version="adapter-2")
    other = cache_key(source_code="source-b", adapter_version="adapter-1")
    for key in (old, active, other):
        await cache.put(key, b"value", ttl_seconds=60, now=NOW)

    removed = await cache.invalidate_adapter("source-a", "adapter-2")

    assert removed == 1
    assert (await cache.get(old, now=NOW)).status is CacheLookupStatus.MISS
    assert (await cache.get(active, now=NOW)).status is CacheLookupStatus.FRESH
    assert (await cache.get(other, now=NOW)).status is CacheLookupStatus.FRESH


def health_policy() -> SourceHealthPolicy:
    return SourceHealthPolicy(2, 2, 100, 20)


def test_source_health_degrades_and_recovers_deterministically() -> None:
    health = SourceHealth(
        1,
        SourceHealthStatus.HEALTHY,
        0,
        0,
        1,
        NOW,
        NOW,
    )
    health = evolve_source_health(
        health,
        IntegrationOutcome.NETWORK_FAILURE,
        health_policy(),
        now=NOW,
    )
    assert health.status is SourceHealthStatus.HEALTHY
    health = evolve_source_health(
        health,
        IntegrationOutcome.UPSTREAM_FAILURE,
        health_policy(),
        now=NOW + timedelta(seconds=1),
    )
    assert health.status is SourceHealthStatus.DEGRADED
    assert health.version == 2
    health = evolve_source_health(
        health,
        IntegrationOutcome.SUCCESS,
        health_policy(),
        now=NOW + timedelta(seconds=2),
    )
    assert health.status is SourceHealthStatus.DEGRADED
    health = evolve_source_health(
        health,
        IntegrationOutcome.SUCCESS,
        health_policy(),
        now=NOW + timedelta(seconds=3),
    )
    assert health.status is SourceHealthStatus.HEALTHY
    assert health.version == 3


class NoopObservabilityRepository:
    async def record(
        self,
        value: IntegrationRequestInput,
        policy: SourceHealthPolicy,
    ) -> tuple[object, object]:
        raise AssertionError


def test_observability_metadata_is_bounded_and_has_no_secret_payload_fields() -> None:
    value = IntegrationRequestInput(
        1,
        str(uuid4()),
        SourceOperation.CHECK_AVAILABILITY,
        IntegrationOutcome.SUCCESS,
        25,
        1,
        512,
        200,
        CacheLookupStatus.MISS,
        None,
        NOW,
    )
    service = IntegrationObservabilityService(  # type: ignore[arg-type]
        NoopObservabilityRepository(),
        health_policy(),
    )
    service._validate(value)

    assert "body" not in value.__dataclass_fields__
    assert "headers" not in value.__dataclass_fields__
    assert "token" not in value.__dataclass_fields__

    with pytest.raises(ValueError):
        service._validate(replace(value, failure_code="Bearer secret"))
