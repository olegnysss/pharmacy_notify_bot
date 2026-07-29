from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.geography import GeographyPolicy, LocationScopeService
from pharmacy_bot.domain.geography import (
    Coordinate,
    LocationScopeInput,
    LocationScopeKind,
    StaleLocationScopeVersion,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.location_scope_repository import (
    SqlAlchemyLocationScopeRepository,
)
from pharmacy_bot.infrastructure.models import (
    LocationScopeModel,
    LocationScopeVersionModel,
    SubscriptionModel,
    SubscriptionSetupDraftModel,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_location_scope_is_idempotent_versioned_and_subscription_ready(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    service = LocationScopeService(
        SqlAlchemyLocationScopeRepository(session_factory),
        GeographyPolicy(min_radius_meters=100, max_radius_meters=100_000),
    )
    now = datetime(2026, 7, 30, tzinfo=UTC)
    radius = LocationScopeInput(
        LocationScopeKind.RADIUS,
        coordinate=Coordinate(Decimal("55.7558"), Decimal("37.6176")),
        radius_meters=5000,
    )
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(SubscriptionModel))
            await session.execute(delete(SubscriptionSetupDraftModel))
            await session.execute(delete(LocationScopeVersionModel))
            await session.execute(delete(LocationScopeModel))

        first, repeated = await asyncio.gather(
            service.create_or_get(radius, now=now),
            service.create_or_get(radius, now=now),
        )
        revised = await service.revise(
            first.id,
            first.version,
            LocationScopeInput(LocationScopeKind.CITY, city_key="Москва"),
            now=now + timedelta(minutes=1),
        )
        with pytest.raises(StaleLocationScopeVersion):
            await service.revise(
                first.id,
                first.version,
                radius,
                now=now,
            )
        async with session_factory() as session:
            scope_count = await session.scalar(select(func.count()).select_from(LocationScopeModel))
            versions = list(
                (
                    await session.scalars(
                        select(LocationScopeVersionModel).order_by(
                            LocationScopeVersionModel.version
                        )
                    )
                ).all()
            )

        assert first.id == repeated.id
        assert scope_count == 1
        assert revised.version == 2
        assert revised.value.city_key == "москва"
        assert [item.version for item in versions] == [1, 2]
        assert versions[0].safe_snapshot["latitude"] == "55.755800"
        assert versions[1].safe_snapshot["city_key"] == "москва"
    finally:
        await engine.dispose()
