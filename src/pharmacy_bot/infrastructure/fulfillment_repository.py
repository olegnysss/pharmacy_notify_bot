from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.domain.fulfillment import (
    FulfillmentInput,
    FulfillmentRecord,
    FulfillmentType,
)
from pharmacy_bot.domain.geography import Coordinate
from pharmacy_bot.infrastructure.models import FulfillmentRecordModel


class SqlAlchemyFulfillmentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(
        self,
        source_product_id: int,
        value: FulfillmentInput,
        reference_key: str,
        fingerprint: str,
        *,
        now: datetime,
    ) -> FulfillmentRecord:
        values = {
            "source_code": value.source_code,
            "pharmacy_id": value.pharmacy_id,
            "latitude": value.coordinate.latitude if value.coordinate else None,
            "longitude": value.coordinate.longitude if value.coordinate else None,
            "delivery_region_key": value.delivery_region_key,
            "delivery_city_key": value.delivery_city_key,
            "fingerprint": fingerprint,
        }
        async with self._session_factory.begin() as session:
            record_id = await session.scalar(
                insert(FulfillmentRecordModel)
                .values(
                    source_product_id=source_product_id,
                    fulfillment_type=value.fulfillment_type.value,
                    reference_key=reference_key,
                    version=1,
                    **values,
                    first_seen_at=now,
                    last_seen_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        FulfillmentRecordModel.source_product_id,
                        FulfillmentRecordModel.fulfillment_type,
                        FulfillmentRecordModel.reference_key,
                    ]
                )
                .returning(FulfillmentRecordModel.id)
            )
            model = cast(
                FulfillmentRecordModel | None,
                await session.scalar(
                    select(FulfillmentRecordModel)
                    .where(
                        FulfillmentRecordModel.source_product_id == source_product_id,
                        FulfillmentRecordModel.fulfillment_type == value.fulfillment_type.value,
                        FulfillmentRecordModel.reference_key == reference_key,
                    )
                    .with_for_update()
                ),
            )
            if model is None:
                raise RuntimeError("fulfillment record was not created or found")
            if record_id is None and model.fingerprint != fingerprint and now >= model.updated_at:
                for key, item in values.items():
                    setattr(model, key, item)
                model.version += 1
                model.updated_at = now
            model.last_seen_at = max(model.last_seen_at, now)
            await session.flush()
            await session.refresh(model)
            return self._snapshot(model)

    @staticmethod
    def _snapshot(model: FulfillmentRecordModel) -> FulfillmentRecord:
        coordinate = (
            Coordinate(model.latitude, model.longitude)
            if model.latitude is not None and model.longitude is not None
            else None
        )
        return FulfillmentRecord(
            model.id,
            model.source_product_id,
            FulfillmentInput(
                FulfillmentType(model.fulfillment_type),
                model.source_code,
                model.pharmacy_id,
                coordinate,
                model.delivery_region_key,
                model.delivery_city_key,
            ),
            model.reference_key,
            model.fingerprint,
            model.version,
            model.first_seen_at,
            model.last_seen_at,
        )
