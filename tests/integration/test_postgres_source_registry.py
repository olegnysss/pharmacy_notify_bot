from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.source_registry import SourceRegistryService
from pharmacy_bot.domain.source_registry import (
    LegalUsageStatus,
    SourceConfiguration,
    SourceLimits,
    SourceOperation,
    SourceRegistryConflict,
    SourceStatus,
    SourceType,
    StaleSourceVersion,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    AdapterIngestionReceiptModel,
    SourceModel,
    SourceVersionModel,
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


def configuration(**changes: object) -> SourceConfiguration:
    value = SourceConfiguration(
        "registry-test",
        "Registry test",
        SourceType.PUBLIC_API,
        SourceStatus.ACTIVE,
        LegalUsageStatus.ALLOWED,
        "adapter-1",
        "capabilities-1",
        frozenset({SourceOperation.HEALTH, SourceOperation.CHECK_AVAILABILITY}),
        ("https://registry-test.example/api",),
        (),
        SourceLimits(30, 60, 3, 300, 30),
    )
    return replace(value, **changes)


async def test_registry_is_idempotent_versioned_and_audited(database_url: str) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    service = SourceRegistryService(SqlAlchemySourceRegistryRepository(session_factory))
    now = datetime(2026, 7, 29, 14, tzinfo=UTC)
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(AdapterIngestionReceiptModel))
            await session.execute(delete(SourceVersionModel))
            await session.execute(delete(SourceModel))

        first, repeated = await asyncio.gather(
            service.create_or_get(configuration(), now=now),
            service.create_or_get(configuration(), now=now),
        )
        assert first.id == repeated.id
        assert first.version == repeated.version == 1

        with pytest.raises(SourceRegistryConflict):
            await service.create_or_get(
                configuration(name="Conflicting registration"),
                now=now,
            )

        disabled = await service.revise(
            first.id,
            1,
            configuration(
                status=SourceStatus.DISABLED,
                legal_status=LegalUsageStatus.BLOCKED,
            ),
            actor_internal_id=42,
            reason="  legal policy changed  ",
            now=now + timedelta(minutes=1),
        )
        unchanged = await service.revise(
            first.id,
            2,
            disabled.configuration,
            actor_internal_id=42,
            reason="idempotent retry",
            now=now + timedelta(minutes=2),
        )
        assert disabled.version == unchanged.version == 2
        assert not SourceRegistryService.operation_decision(
            disabled.configuration,
            SourceOperation.HEALTH,
        ).allowed

        with pytest.raises(StaleSourceVersion):
            await service.revise(
                first.id,
                1,
                configuration(),
                actor_internal_id=42,
                reason="stale update",
                now=now + timedelta(minutes=3),
            )

        async with session_factory() as session:
            source_count = await session.scalar(select(func.count()).select_from(SourceModel))
            versions = (
                await session.scalars(
                    select(SourceVersionModel).order_by(SourceVersionModel.version)
                )
            ).all()

        assert source_count == 1
        assert len(versions) == 2
        assert versions[0].reason == "registered"
        assert versions[0].actor_internal_id is None
        assert versions[1].reason == "legal policy changed"
        assert versions[1].actor_internal_id == 42
        assert "secret" not in versions[1].safe_snapshot
        assert versions[1].safe_snapshot["legal_status"] == "blocked"
    finally:
        await engine.dispose()
