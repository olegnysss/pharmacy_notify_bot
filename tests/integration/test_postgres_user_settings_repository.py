from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import (
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
)
from pharmacy_bot.domain.user_settings import SettingsStatus
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    AuditLogModel,
    ConsentDecisionModel,
    SubscriptionEditDraftModel,
    SubscriptionModel,
    SubscriptionSetupDraftModel,
    UserModel,
    UserPreferencesModel,
)
from pharmacy_bot.infrastructure.onboarding_repository import (
    SqlAlchemyOnboardingRepository,
)
from pharmacy_bot.infrastructure.user_settings_repository import (
    SqlAlchemyUserSettingsRepository,
)
from tests.integration.test_postgres_subscription_repository import model

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_preferences_are_versioned_and_do_not_rewrite_subscriptions(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    onboarding = SqlAlchemyOnboardingRepository(session_factory)
    repository = SqlAlchemyUserSettingsRepository(session_factory)
    now = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(AuditLogModel))
            await session.execute(delete(SubscriptionEditDraftModel))
            await session.execute(delete(SubscriptionModel))
            await session.execute(delete(SubscriptionSetupDraftModel))
            await session.execute(delete(UserPreferencesModel))
            await session.execute(delete(ConsentDecisionModel))
            await session.execute(delete(UserModel))
        user = await onboarding.get_or_create_user(TelegramIdentity(930, 930))
        preferences = await repository.get_or_create(user.id)
        location = LocationCandidate(
            "city:kazan",
            LocationInputMode.CITY,
            "Казань",
            city="Казань",
            confidence=LocationConfidence.EXACT,
        )
        saved = await repository.save(
            replace(
                preferences,
                default_location=location,
                default_radius_meters=5000,
                default_source_codes=("source-a",),
                status=SettingsStatus.IDLE,
            ),
            expected_generation=preferences.generation,
        )
        assert saved
        async with session_factory.begin() as session:
            existing = model(user.id, 301, "settings-existing", now)
            session.add(existing)
        usage = await repository.usage(user.id, 20)
        cleared = await repository.save(
            replace(
                saved,
                default_location=None,
                default_radius_meters=None,
                default_source_codes=(),
            ),
            expected_generation=saved.generation,
        )
        async with session_factory() as session:
            stored = await session.scalar(
                select(SubscriptionModel).where(SubscriptionModel.id == existing.id)
            )

        assert cleared
        assert usage.active_subscriptions == 1
        assert stored
        assert stored.location_display_name == "Москва"
        assert stored.radius_meters == 5000
    finally:
        await engine.dispose()
