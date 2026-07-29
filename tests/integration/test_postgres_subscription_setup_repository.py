from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    MonitoringFilters,
    ProductSnapshot,
    SetupStatus,
    SourceOption,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    ConsentDecisionModel,
    SubscriptionModel,
    SubscriptionSetupDraftModel,
    UserModel,
)
from pharmacy_bot.infrastructure.onboarding_repository import (
    SqlAlchemyOnboardingRepository,
)
from pharmacy_bot.infrastructure.subscription_setup_repository import (
    SqlAlchemySubscriptionSetupRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_postgres_confirmation_is_idempotent_and_initial_state_is_pending(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    onboarding = SqlAlchemyOnboardingRepository(session_factory)
    repository = SqlAlchemySubscriptionSetupRepository(session_factory)
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    product = ProductSnapshot(
        "product-1",
        "v1",
        "Товар",
        "таблетки",
        "10 мг",
        "№20",
        "Производитель",
        "source.example",
    )
    location = LocationCandidate(
        "city:moscow",
        LocationInputMode.CITY,
        "Москва",
        city="Москва",
        confidence=LocationConfidence.EXACT,
    )

    try:
        async with session_factory.begin() as session:
            await session.execute(delete(SubscriptionModel))
            await session.execute(delete(SubscriptionSetupDraftModel))
            await session.execute(delete(ConsentDecisionModel))
            await session.execute(delete(UserModel))
        user = await onboarding.get_or_create_user(
            TelegramIdentity(telegram_user_id=701, telegram_chat_id=701)
        )
        draft = await repository.start_or_resume(
            user.id,
            product,
            now=now,
            expires_at=now + timedelta(hours=2),
        )
        review = replace(
            draft,
            status=SetupStatus.REVIEW,
            location_mode=LocationInputMode.CITY,
            location_candidates=(location,),
            location=location,
            radius_meters=5000,
            available_sources=(SourceOption("source-a", "Аптека A", True, ordinal=0),),
            selected_source_codes=("source-a",),
            filters=MonitoringFilters(notify_low_stock=True),
            completion_mode=CompletionMode.CONTINUE,
        )
        saved = await repository.save(review, expected_generation=draft.generation)
        assert saved is not None

        first = await repository.create_subscription(
            user.id,
            expected_generation=saved.generation,
            now=now,
        )
        assert first is not None
        second = await repository.create_subscription(
            user.id,
            expected_generation=first[0].generation,
            now=now,
        )
        async with session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(SubscriptionModel))

        assert second is not None
        assert first[1].id == second[1].id
        assert first[1].availability_state is AvailabilityState.PENDING
        assert first[0].status is SetupStatus.CREATED
        assert count == 1

        next_draft = await repository.start_or_resume(
            user.id,
            product,
            now=now,
            expires_at=now + timedelta(hours=2),
        )
        assert next_draft.status is SetupStatus.CHOOSE_LOCATION
        assert next_draft.idempotency_key != saved.idempotency_key
    finally:
        await engine.dispose()
