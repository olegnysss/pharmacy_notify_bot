from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.geography import GeographyPolicy
from pharmacy_bot.application.pharmacy_directory import PharmacyDirectoryService
from pharmacy_bot.domain.geography import Coordinate
from pharmacy_bot.domain.pharmacy_directory import (
    PharmacyIdentity,
    PharmacyMappingActor,
    PharmacyMatchLevel,
    PharmacyStatus,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    PharmacyMappingDecisionModel,
    PharmacyModel,
    PharmacyVersionModel,
    SourcePharmacyModel,
    SourcePharmacyVersionModel,
)
from pharmacy_bot.infrastructure.pharmacy_directory_repository import (
    SqlAlchemyPharmacyDirectoryRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


def identity(
    name: str = "Аптека 1",
    *,
    latitude: str | None = "55.757",
    longitude: str | None = "37.615",
    address: str = "Москва, Тверская улица, 1",
) -> PharmacyIdentity:
    coordinate = (
        Coordinate(Decimal(latitude), Decimal(longitude))
        if latitude is not None and longitude is not None
        else None
    )
    return PharmacyIdentity(
        name,
        address,
        "network-a",
        coordinate,
    )


async def test_directory_upsert_mapping_and_radius_pagination(database_url: str) -> None:
    engine = create_engine(database_url)
    factory = create_session_factory(engine)
    repository = SqlAlchemyPharmacyDirectoryRepository(factory)
    service = PharmacyDirectoryService(repository, GeographyPolicy(), max_page_size=2)
    now = datetime(2026, 7, 30, 2, tzinfo=UTC)
    try:
        async with factory.begin() as session:
            await session.execute(delete(PharmacyMappingDecisionModel))
            await session.execute(delete(SourcePharmacyVersionModel))
            await session.execute(delete(SourcePharmacyModel))
            await session.execute(delete(PharmacyVersionModel))
            await session.execute(delete(PharmacyModel))

        canonical = await service.create_or_get(identity(), PharmacyStatus.ACTIVE, now=now)
        second = await service.create_or_get(
            identity("Аптека 2", latitude="55.758", longitude="37.615"),
            PharmacyStatus.ACTIVE,
            now=now,
        )
        await service.create_or_get(
            identity("Без координат", latitude=None, longitude=None),
            PharmacyStatus.ACTIVE,
            now=now,
        )
        first_source, repeated_source = await asyncio.gather(
            service.ingest_source(
                "source-a", "point-1", identity(), PharmacyStatus.ACTIVE, now=now
            ),
            service.ingest_source(
                "source-a", "point-1", identity(), PharmacyStatus.ACTIVE, now=now
            ),
        )
        address_changed = await service.ingest_source(
            "source-a",
            "point-1",
            identity(address="Москва, Тверская улица, 1А"),
            PharmacyStatus.ACTIVE,
            now=now + timedelta(minutes=1),
        )
        changed = await service.ingest_source(
            "source-a",
            "point-1",
            identity("Новое имя"),
            PharmacyStatus.ACTIVE,
            now=now + timedelta(minutes=2),
        )
        match = service.match(changed.identity, canonical.identity)
        actor = PharmacyMappingActor(77, frozenset({"pharmacy_mapping"}))
        linked = await service.confirm_mapping(
            changed.id,
            canonical.id,
            match,
            actor,
            "confirm-point-1",
            now=now,
        )
        repeated_link = await service.confirm_mapping(
            changed.id,
            canonical.id,
            match,
            actor,
            "confirm-point-1",
            now=now,
        )
        revoked = await service.revoke_mapping(
            changed.id,
            linked.mapping_version,
            actor,
            "revoke-point-1",
            now=now,
        )
        page1 = await service.search_radius(
            Coordinate(Decimal("55.757"), Decimal("37.615")),
            1_000,
            page_size=1,
        )
        page2 = await service.search_radius(
            Coordinate(Decimal("55.757"), Decimal("37.615")),
            1_000,
            cursor=page1.next_cursor,
            page_size=1,
        )
        async with factory() as session:
            source_count = await session.scalar(
                select(func.count()).select_from(SourcePharmacyModel)
            )
            version_count = await session.scalar(
                select(func.count()).select_from(SourcePharmacyVersionModel)
            )
            decision_count = await session.scalar(
                select(func.count()).select_from(PharmacyMappingDecisionModel)
            )

        assert canonical.id != second.id
        assert first_source.id == repeated_source.id == changed.id
        assert address_changed.version == 2
        assert address_changed.identity.normalized_address.endswith("1а")
        assert changed.version == 3
        assert source_count == 1
        assert version_count == 3
        assert match.level is PharmacyMatchLevel.EXACT
        assert linked.canonical_pharmacy_id == canonical.id
        assert repeated_link.mapping_version == linked.mapping_version
        assert revoked.canonical_pharmacy_id is None
        assert decision_count == 2
        assert len(page1.items) == len(page2.items) == 1
        assert page1.items[0].pharmacy.id != page2.items[0].pharmacy.id
        assert page1.next_cursor is not None
        assert page2.next_cursor is None
    finally:
        await engine.dispose()
