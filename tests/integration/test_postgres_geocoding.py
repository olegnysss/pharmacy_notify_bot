from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete

from pharmacy_bot.application.geocoding import (
    GeocodingService,
    ProviderCandidate,
    ProviderGeocodingResult,
)
from pharmacy_bot.application.geography import GeographyPolicy
from pharmacy_bot.domain.geocoding import GeocodingConflict, GeocodingDecision, GeocodingPrecision
from pharmacy_bot.domain.geography import Coordinate
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.geocoding_repository import (
    SqlAlchemyGeocodingSessionRepository,
)
from pharmacy_bot.infrastructure.models import GeocodingSessionModel, UserModel

pytestmark = pytest.mark.integration


class Provider:
    async def geocode(
        self, query: str, *, locale: str, region_hint: str | None
    ) -> ProviderGeocodingResult:
        del query, locale, region_hint
        return ProviderGeocodingResult(
            "provider",
            "2026-07",
            (
                ProviderCandidate(
                    "result-1",
                    "Москва, Тверская улица, 1",
                    Coordinate(Decimal("55.757"), Decimal("37.615")),
                    GeocodingPrecision.ADDRESS,
                ),
            ),
        )


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_confirmation_is_owned_expiring_and_idempotent(database_url: str) -> None:
    engine = create_engine(database_url)
    factory = create_session_factory(engine)
    repository = SqlAlchemyGeocodingSessionRepository(factory)
    service = GeocodingService(Provider(), repository, GeographyPolicy())
    now = datetime(2026, 7, 30, 1, tzinfo=UTC)
    try:
        async with factory.begin() as session:
            await session.execute(delete(GeocodingSessionModel))
            await session.execute(delete(UserModel))
            session.add_all(
                (
                    UserModel(
                        telegram_user_id=7001, telegram_chat_id=7001, onboarding_status="active"
                    ),
                    UserModel(
                        telegram_user_id=7002, telegram_chat_id=7002, onboarding_status="active"
                    ),
                )
            )
        async with factory() as session:
            users = list(
                (await session.execute(__import__("sqlalchemy").select(UserModel))).scalars()
            )
        result = await service.resolve(
            users[0].id, 3, "Москва Тверская 1", locale="ru", region_hint="moscow", now=now
        )
        selected = await service.confirm(users[0].id, 3, result.candidates[0].candidate_id, now=now)
        repeated = await service.confirm(
            users[0].id, 3, result.candidates[0].candidate_id, now=now + timedelta(seconds=1)
        )
        with pytest.raises(GeocodingConflict):
            await service.confirm(users[1].id, 3, result.candidates[0].candidate_id, now=now)

        assert result.decision is GeocodingDecision.EXACT
        assert selected.session_id == repeated.session_id
        assert selected.provider_data_version == "2026-07"
        assert selected.candidate.normalized_address == "Москва, Тверская улица, 1"
    finally:
        await engine.dispose()
