from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.fulfillment import FulfillmentService
from pharmacy_bot.application.geography import GeographyPolicy
from pharmacy_bot.domain.fulfillment import FulfillmentInput, FulfillmentType
from pharmacy_bot.domain.geography import Coordinate
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.fulfillment_repository import (
    SqlAlchemyFulfillmentRepository,
)
from pharmacy_bot.infrastructure.models import (
    FulfillmentRecordModel,
    PharmacyModel,
    SourceProductModel,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_fulfillment_upsert_is_idempotent_and_versioned(database_url: str) -> None:
    engine = create_engine(database_url)
    factory = create_session_factory(engine)
    service = FulfillmentService(
        SqlAlchemyFulfillmentRepository(factory),
        GeographyPolicy(),
    )
    now = datetime(2026, 7, 30, 3, tzinfo=UTC)
    try:
        async with factory.begin() as session:
            await session.execute(delete(FulfillmentRecordModel))
            await session.execute(
                delete(SourceProductModel).where(
                    SourceProductModel.external_id == "fulfillment-product"
                )
            )
            await session.execute(
                delete(PharmacyModel).where(PharmacyModel.fingerprint == "f" * 64)
            )
            pharmacy = PharmacyModel(
                version=1,
                kind="pharmacy",
                status="active",
                fingerprint="f" * 64,
                name="Аптека",
                normalized_address="москва адрес",
                network_key="network",
                latitude=Decimal("55.75"),
                longitude=Decimal("37.61"),
                created_at=now,
                updated_at=now,
            )
            source_product = SourceProductModel(
                source_code="source",
                external_id="fulfillment-product",
                canonical_url="https://pharmacy.example/product",
                raw_name="Товар",
                parsed_attributes={"kind": "medicine"},
                status="active",
                semantic_fingerprint="e" * 64,
                search_document="товар",
                version=1,
                monitoring_eligibility="eligible",
                last_revalidated_version=1,
                fresh_check_required=False,
                first_seen_at=now,
                last_seen_at=now,
                updated_at=now,
            )
            session.add_all((pharmacy, source_product))
            await session.flush()
            pharmacy_id = pharmacy.id
            source_product_id = source_product.id

        coordinate = Coordinate(Decimal("55.75"), Decimal("37.61"))
        value = FulfillmentInput(
            FulfillmentType.PHYSICAL_STOCK,
            "source",
            pharmacy_id=pharmacy_id,
            coordinate=coordinate,
        )
        first, repeated = await asyncio.gather(
            service.upsert(source_product_id, value, now=now),
            service.upsert(source_product_id, value, now=now),
        )
        changed = await service.upsert(
            source_product_id,
            FulfillmentInput(
                FulfillmentType.PHYSICAL_STOCK,
                "source-v2",
                pharmacy_id=pharmacy_id,
                coordinate=coordinate,
            ),
            now=now + timedelta(minutes=1),
        )
        online = await service.upsert(
            source_product_id,
            FulfillmentInput(FulfillmentType.ONLINE_UNKNOWN, "source"),
            now=now,
        )
        async with factory() as session:
            count = await session.scalar(select(func.count()).select_from(FulfillmentRecordModel))

        assert first.id == repeated.id == changed.id
        assert first.version == repeated.version == 1
        assert changed.version == 2
        assert changed.value.source_code == "source-v2"
        assert online.reference_key == "online:unknown"
        assert count == 2
    finally:
        await engine.dispose()
