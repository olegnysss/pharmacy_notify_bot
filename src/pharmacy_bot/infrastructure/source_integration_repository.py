from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.source_integration import evolve_source_health
from pharmacy_bot.domain.source_integration import (
    CacheLookupStatus,
    IntegrationOutcome,
    IntegrationRequestFact,
    IntegrationRequestInput,
    SourceHealth,
    SourceHealthPolicy,
    SourceHealthStatus,
    WebhookReceipt,
    WebhookReceiptStatus,
    WebhookRejected,
    WebhookRejectionKind,
)
from pharmacy_bot.domain.source_registry import SourceOperation
from pharmacy_bot.infrastructure.models import (
    IntegrationRequestModel,
    SourceHealthEventModel,
    SourceHealthModel,
    WebhookReceiptModel,
)


class SqlAlchemyWebhookReceiptRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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
        async with self._session_factory.begin() as session:
            receipt_id = await session.scalar(
                insert(WebhookReceiptModel)
                .values(
                    source_id=source_id,
                    delivery_key=delivery_key,
                    body_digest=body_digest,
                    event_timestamp=event_timestamp,
                    body_bytes=body_bytes,
                    status=WebhookReceiptStatus.PROCESSING.value,
                    received_at=received_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        WebhookReceiptModel.source_id,
                        WebhookReceiptModel.delivery_key,
                    ]
                )
                .returning(WebhookReceiptModel.id)
            )
            created = receipt_id is not None
            if created:
                model = await session.get(WebhookReceiptModel, receipt_id)
            else:
                model = await session.scalar(
                    select(WebhookReceiptModel).where(
                        WebhookReceiptModel.source_id == source_id,
                        WebhookReceiptModel.delivery_key == delivery_key,
                    )
                )
            if model is None:
                raise RuntimeError("webhook receipt was not created or found")
            if model.body_digest != body_digest:
                raise WebhookRejected(
                    WebhookRejectionKind.REPLAY_CONFLICT,
                    "webhook delivery key was reused for another payload",
                )
            return self._webhook(model), created

    async def accept(
        self,
        receipt_id: int,
        business_fingerprint: str,
        *,
        completed_at: datetime,
    ) -> WebhookReceipt:
        return await self._complete(
            receipt_id,
            WebhookReceiptStatus.ACCEPTED,
            business_fingerprint=business_fingerprint,
            quarantine_reason=None,
            completed_at=completed_at,
        )

    async def quarantine(
        self,
        receipt_id: int,
        reason_code: str,
        *,
        completed_at: datetime,
    ) -> WebhookReceipt:
        return await self._complete(
            receipt_id,
            WebhookReceiptStatus.QUARANTINED,
            business_fingerprint=None,
            quarantine_reason=reason_code,
            completed_at=completed_at,
        )

    async def _complete(
        self,
        receipt_id: int,
        status: WebhookReceiptStatus,
        *,
        business_fingerprint: str | None,
        quarantine_reason: str | None,
        completed_at: datetime,
    ) -> WebhookReceipt:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(WebhookReceiptModel)
                .where(WebhookReceiptModel.id == receipt_id)
                .with_for_update()
            )
            if model is None:
                raise RuntimeError("webhook receipt does not exist")
            if model.status == WebhookReceiptStatus.PROCESSING.value:
                model.status = status.value
                model.business_fingerprint = business_fingerprint
                model.quarantine_reason = quarantine_reason
                model.completed_at = completed_at
                await session.flush()
                return self._webhook(model)
            if (
                model.status != status.value
                or model.business_fingerprint != business_fingerprint
                or model.quarantine_reason != quarantine_reason
            ):
                raise RuntimeError("webhook receipt has another terminal result")
            return self._webhook(model)

    @staticmethod
    def _webhook(model: WebhookReceiptModel) -> WebhookReceipt:
        return WebhookReceipt(
            model.id,
            model.source_id,
            model.delivery_key,
            model.body_digest,
            model.event_timestamp,
            model.body_bytes,
            WebhookReceiptStatus(model.status),
            model.business_fingerprint,
            model.quarantine_reason,
            model.received_at,
            model.completed_at,
        )


class SqlAlchemyIntegrationObservabilityRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        value: IntegrationRequestInput,
        policy: SourceHealthPolicy,
    ) -> tuple[IntegrationRequestFact, SourceHealth]:
        async with self._session_factory.begin() as session:
            request_id = await session.scalar(
                insert(IntegrationRequestModel)
                .values(
                    source_id=value.source_id,
                    correlation_id=value.correlation_id,
                    operation=value.operation.value,
                    outcome=value.outcome.value,
                    duration_ms=value.duration_ms,
                    attempts=value.attempts,
                    response_bytes=value.response_bytes,
                    http_status=value.http_status,
                    cache_status=(
                        value.cache_status.value if value.cache_status is not None else None
                    ),
                    failure_code=value.failure_code,
                    occurred_at=value.occurred_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        IntegrationRequestModel.source_id,
                        IntegrationRequestModel.correlation_id,
                    ]
                )
                .returning(IntegrationRequestModel.id)
            )
            created = request_id is not None
            if created:
                request_model = await session.get(IntegrationRequestModel, request_id)
            else:
                request_model = await session.scalar(
                    select(IntegrationRequestModel).where(
                        IntegrationRequestModel.source_id == value.source_id,
                        IntegrationRequestModel.correlation_id == value.correlation_id,
                    )
                )
            if request_model is None:
                raise RuntimeError("integration request was not stored or found")
            if not created and not self._same_request(request_model, value):
                raise ValueError("integration correlation id was reused")

            await session.execute(
                insert(SourceHealthModel)
                .values(
                    source_id=value.source_id,
                    status=SourceHealthStatus.HEALTHY.value,
                    consecutive_failures=0,
                    consecutive_successes=0,
                    version=1,
                    changed_at=value.occurred_at,
                    updated_at=value.occurred_at,
                )
                .on_conflict_do_nothing(index_elements=[SourceHealthModel.source_id])
            )
            health_model = await session.scalar(
                select(SourceHealthModel)
                .where(SourceHealthModel.source_id == value.source_id)
                .with_for_update()
            )
            if health_model is None:
                raise RuntimeError("source health was not created or found")
            if created:
                before = self._health(health_model)
                effective_time = max(value.occurred_at, health_model.updated_at)
                after = evolve_source_health(
                    before,
                    value.outcome,
                    policy,
                    now=effective_time,
                )
                self._apply_health(health_model, after)
                if after.version != before.version:
                    session.add(
                        SourceHealthEventModel(
                            source_id=value.source_id,
                            version=after.version,
                            status=after.status.value,
                            reason_code=(
                                "degraded_threshold"
                                if after.status is SourceHealthStatus.DEGRADED
                                else "recovered_threshold"
                            ),
                            occurred_at=effective_time,
                        )
                    )
            await session.flush()
            await self._retain_requests(session, value.source_id, policy)
            await self._retain_events(session, value.source_id, policy)
            return self._request(request_model), self._health(health_model)

    @staticmethod
    def _same_request(
        model: IntegrationRequestModel,
        value: IntegrationRequestInput,
    ) -> bool:
        return (
            model.operation == value.operation.value
            and model.outcome == value.outcome.value
            and model.duration_ms == value.duration_ms
            and model.attempts == value.attempts
            and model.response_bytes == value.response_bytes
            and model.http_status == value.http_status
            and model.cache_status == (value.cache_status.value if value.cache_status else None)
            and model.failure_code == value.failure_code
            and model.occurred_at == value.occurred_at
        )

    @staticmethod
    def _apply_health(model: SourceHealthModel, value: SourceHealth) -> None:
        model.status = value.status.value
        model.consecutive_failures = value.consecutive_failures
        model.consecutive_successes = value.consecutive_successes
        model.version = value.version
        model.changed_at = value.changed_at
        model.updated_at = value.updated_at

    @staticmethod
    async def _retain_requests(
        session: AsyncSession,
        source_id: int,
        policy: SourceHealthPolicy,
    ) -> None:
        old_ids = (
            select(IntegrationRequestModel.id)
            .where(IntegrationRequestModel.source_id == source_id)
            .order_by(
                IntegrationRequestModel.occurred_at.desc(),
                IntegrationRequestModel.id.desc(),
            )
            .offset(policy.retained_requests_per_source)
        )
        await session.execute(
            delete(IntegrationRequestModel).where(IntegrationRequestModel.id.in_(old_ids))
        )

    @staticmethod
    async def _retain_events(
        session: AsyncSession,
        source_id: int,
        policy: SourceHealthPolicy,
    ) -> None:
        old_ids = (
            select(SourceHealthEventModel.id)
            .where(SourceHealthEventModel.source_id == source_id)
            .order_by(
                SourceHealthEventModel.occurred_at.desc(),
                SourceHealthEventModel.id.desc(),
            )
            .offset(policy.retained_transitions_per_source)
        )
        await session.execute(
            delete(SourceHealthEventModel).where(SourceHealthEventModel.id.in_(old_ids))
        )

    @staticmethod
    def _request(model: IntegrationRequestModel) -> IntegrationRequestFact:
        return IntegrationRequestFact(
            model.id,
            model.source_id,
            model.correlation_id,
            SourceOperation(model.operation),
            IntegrationOutcome(model.outcome),
            model.duration_ms,
            model.attempts,
            model.response_bytes,
            model.http_status,
            CacheLookupStatus(model.cache_status) if model.cache_status else None,
            model.failure_code,
            model.occurred_at,
        )

    @staticmethod
    def _health(model: SourceHealthModel) -> SourceHealth:
        return SourceHealth(
            model.source_id,
            SourceHealthStatus(model.status),
            model.consecutive_failures,
            model.consecutive_successes,
            model.version,
            model.changed_at,
            model.updated_at,
        )
