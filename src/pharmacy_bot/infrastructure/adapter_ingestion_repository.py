from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.adapter_contract import (
    decode_envelope,
    encode_envelope,
)
from pharmacy_bot.domain.adapter_contract import (
    AdapterContractError,
    AdapterEnvelope,
    AdapterErrorKind,
    AdapterIngestionReceipt,
    AdapterRequest,
)
from pharmacy_bot.infrastructure.models import AdapterIngestionReceiptModel


class SqlAlchemyAdapterIngestionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(
        self,
        source_id: int,
        idempotency_key: str,
    ) -> AdapterIngestionReceipt | None:
        async with self._session_factory() as session:
            model = await session.scalar(
                select(AdapterIngestionReceiptModel).where(
                    AdapterIngestionReceiptModel.source_id == source_id,
                    AdapterIngestionReceiptModel.idempotency_key == idempotency_key,
                )
            )
            return self._snapshot(model) if model is not None else None

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
        values = {
            "source_id": source_id,
            "idempotency_key": request.context.idempotency_key,
            "request_fingerprint": request_fingerprint,
            "result_fingerprint": result_fingerprint,
            "correlation_id": request.context.correlation_id,
            "causation_id": request.context.causation_id,
            "operation": request.operation.value,
            "source_code": envelope.source_code,
            "adapter_version": envelope.adapter_version,
            "contract_version": envelope.contract_version,
            "schema_version": envelope.schema_version,
            "safe_result": encode_envelope(envelope),
            "created_at": now,
        }
        async with self._session_factory.begin() as session:
            receipt_id = await session.scalar(
                insert(AdapterIngestionReceiptModel)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[
                        AdapterIngestionReceiptModel.source_id,
                        AdapterIngestionReceiptModel.idempotency_key,
                    ]
                )
                .returning(AdapterIngestionReceiptModel.id)
            )
            if receipt_id is not None:
                model = await session.get(AdapterIngestionReceiptModel, receipt_id)
            else:
                model = await session.scalar(
                    select(AdapterIngestionReceiptModel).where(
                        AdapterIngestionReceiptModel.source_id == source_id,
                        AdapterIngestionReceiptModel.idempotency_key
                        == request.context.idempotency_key,
                    )
                )
            if model is None:
                raise RuntimeError("adapter receipt was not stored or found")
            if model.request_fingerprint != request_fingerprint:
                raise AdapterContractError(
                    AdapterErrorKind.IDEMPOTENCY_CONFLICT,
                    "adapter idempotency key was reused for another request",
                )
            return self._snapshot(model)

    @staticmethod
    def _snapshot(model: AdapterIngestionReceiptModel) -> AdapterIngestionReceipt:
        return AdapterIngestionReceipt(
            model.id,
            model.source_id,
            model.idempotency_key,
            model.request_fingerprint,
            model.result_fingerprint,
            model.correlation_id,
            model.causation_id,
            decode_envelope(model.safe_result),
            model.created_at,
        )
