from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.source_integration import (
    HmacSha256WebhookAuthenticator,
    IntegrationObservabilityService,
    WebhookIntakeService,
)
from pharmacy_bot.application.source_registry import SourceRegistryService
from pharmacy_bot.domain.source_integration import (
    CacheLookupStatus,
    IntegrationOutcome,
    IntegrationRequestInput,
    SourceHealthPolicy,
    SourceHealthStatus,
    WebhookPayloadRejected,
    WebhookPolicy,
    WebhookReceipt,
    WebhookReceiveOutcome,
    WebhookRejected,
    WebhookRejectionKind,
)
from pharmacy_bot.domain.source_registry import (
    LegalUsageStatus,
    SourceConfiguration,
    SourceLimits,
    SourceOperation,
    SourceStatus,
    SourceType,
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
from pharmacy_bot.infrastructure.source_integration_repository import (
    SqlAlchemyIntegrationObservabilityRepository,
    SqlAlchemyWebhookReceiptRepository,
)
from pharmacy_bot.infrastructure.source_registry_repository import (
    SqlAlchemySourceRegistryRepository,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 7, 29, 19, tzinfo=UTC)
SECRET = b"integration webhook secret"


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


def configuration(code: str) -> SourceConfiguration:
    return SourceConfiguration(
        code,
        f"Source {code}",
        SourceType.PARTNER_API,
        SourceStatus.ACTIVE,
        LegalUsageStatus.ALLOWED,
        "adapter-1",
        "capabilities-1",
        frozenset({SourceOperation.HEALTH, SourceOperation.RECEIVE_WEBHOOK}),
        (f"https://{code}.example/api",),
        (),
        SourceLimits(100, 60, 5, 300, 30),
    )


class CountingProcessor:
    def __init__(self, *, reject: bool = False) -> None:
        self.calls = 0
        self.reject = reject

    async def process(self, body: bytes, receipt: WebhookReceipt) -> str:
        del receipt
        self.calls += 1
        await asyncio.sleep(0.01)
        if self.reject:
            raise WebhookPayloadRejected("invalid_schema")
        return sha256(b"normalized:" + body).hexdigest()


def metric(
    source_id: int,
    outcome: IntegrationOutcome,
    *,
    correlation: str | None = None,
    at: datetime = NOW,
) -> IntegrationRequestInput:
    return IntegrationRequestInput(
        source_id,
        correlation or str(uuid4()),
        SourceOperation.HEALTH,
        outcome,
        25,
        1,
        128,
        200 if outcome is IntegrationOutcome.SUCCESS else 503,
        CacheLookupStatus.MISS,
        None if outcome is IntegrationOutcome.SUCCESS else "source_failure",
        at,
    )


async def cleanup(session_factory: object) -> None:
    # The concrete factory type is intentionally inferred at the call site.
    async with session_factory.begin() as session:  # type: ignore[attr-defined]
        await session.execute(delete(SourceHealthEventModel))
        await session.execute(delete(SourceHealthModel))
        await session.execute(delete(IntegrationRequestModel))
        await session.execute(delete(WebhookReceiptModel))
        await session.execute(delete(AdapterIngestionReceiptModel))
        await session.execute(delete(SourceVersionModel))
        await session.execute(delete(SourceModel))


async def test_webhook_receipts_and_source_health_are_atomic_and_isolated(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    registry = SourceRegistryService(SqlAlchemySourceRegistryRepository(session_factory))
    try:
        await cleanup(session_factory)
        source_a = await registry.create_or_get(configuration("integration-a"), now=NOW)
        source_b = await registry.create_or_get(configuration("integration-b"), now=NOW)
        webhook = WebhookIntakeService(SqlAlchemyWebhookReceiptRepository(session_factory))
        body = b'{"event":"availability"}'
        timestamp = str(int(NOW.timestamp()))
        signature = HmacSha256WebhookAuthenticator.sign(SECRET, timestamp, body)
        processor = CountingProcessor()
        arguments = {
            "secret": SECRET,
            "signature": signature,
            "timestamp": timestamp,
            "delivery_key": "delivery-concurrent",
            "content_type": "application/json",
            "body": body,
            "now": NOW,
        }

        first, second = await asyncio.gather(
            webhook.receive(
                source_a,
                WebhookPolicy(10_000, 300, 30),
                processor,
                **arguments,  # type: ignore[arg-type]
            ),
            webhook.receive(
                source_a,
                WebhookPolicy(10_000, 300, 30),
                processor,
                **arguments,  # type: ignore[arg-type]
            ),
        )
        assert {first.outcome, second.outcome} == {
            WebhookReceiveOutcome.ACCEPTED,
            WebhookReceiveOutcome.DUPLICATE,
        }
        assert first.receipt.id == second.receipt.id
        assert processor.calls == 1

        with pytest.raises(WebhookRejected) as bad_signature:
            await webhook.receive(
                source_a,
                WebhookPolicy(10_000, 300, 30),
                CountingProcessor(),
                **{**arguments, "signature": "sha256=" + "0" * 64, "delivery_key": "bad"},
            )  # type: ignore[arg-type]
        assert bad_signature.value.kind is WebhookRejectionKind.INVALID_SIGNATURE

        quarantined = await webhook.receive(
            source_a,
            WebhookPolicy(10_000, 300, 30),
            CountingProcessor(reject=True),
            **{**arguments, "delivery_key": "delivery-invalid"},
        )  # type: ignore[arg-type]
        assert quarantined.outcome is WebhookReceiveOutcome.QUARANTINED

        observability = IntegrationObservabilityService(
            SqlAlchemyIntegrationObservabilityRepository(session_factory),
            SourceHealthPolicy(2, 2, 3, 2),
        )
        first_failure = metric(
            source_a.id,
            IntegrationOutcome.NETWORK_FAILURE,
            at=NOW + timedelta(seconds=1),
        )
        _, health = await observability.record(first_failure)
        _, duplicate_health = await observability.record(first_failure)
        assert duplicate_health == health
        _, degraded = await observability.record(
            metric(
                source_a.id,
                IntegrationOutcome.UPSTREAM_FAILURE,
                at=NOW + timedelta(seconds=2),
            )
        )
        _, isolated = await observability.record(
            metric(
                source_b.id,
                IntegrationOutcome.SUCCESS,
                at=NOW + timedelta(seconds=2),
            )
        )
        assert degraded.status is SourceHealthStatus.DEGRADED
        assert isolated.status is SourceHealthStatus.HEALTHY
        _, still_degraded = await observability.record(
            metric(
                source_a.id,
                IntegrationOutcome.SUCCESS,
                at=NOW + timedelta(seconds=3),
            )
        )
        _, recovered = await observability.record(
            metric(
                source_a.id,
                IntegrationOutcome.SUCCESS,
                at=NOW + timedelta(seconds=4),
            )
        )
        assert still_degraded.status is SourceHealthStatus.DEGRADED
        assert recovered.status is SourceHealthStatus.HEALTHY
        assert recovered.version == 3

        async with session_factory() as session:
            webhook_count = await session.scalar(
                select(func.count()).select_from(WebhookReceiptModel)
            )
            request_count_a = await session.scalar(
                select(func.count())
                .select_from(IntegrationRequestModel)
                .where(IntegrationRequestModel.source_id == source_a.id)
            )
            transition_count = await session.scalar(
                select(func.count())
                .select_from(SourceHealthEventModel)
                .where(SourceHealthEventModel.source_id == source_a.id)
            )
            stored_webhook = await session.scalar(
                select(WebhookReceiptModel).where(
                    WebhookReceiptModel.delivery_key == "delivery-concurrent"
                )
            )

        assert webhook_count == 2
        assert request_count_a == 3
        assert transition_count == 2
        assert stored_webhook is not None
        assert stored_webhook.body_bytes == len(body)
        assert "availability" not in repr(stored_webhook.__dict__)
        assert SECRET.decode() not in repr(stored_webhook.__dict__)
    finally:
        await cleanup(session_factory)
        await engine.dispose()
