from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from pharmacy_bot.application.subscriptions import CheckGate, SubscriptionFilter
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    CompletionMode,
    LocationInputMode,
    SubscriptionStatus,
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
from pharmacy_bot.infrastructure.subscription_repository import (
    SqlAlchemySubscriptionRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


def model(user_id: int, setup_id: int, creation_key: str, now: datetime) -> SubscriptionModel:
    return SubscriptionModel(
        setup_draft_id=setup_id,
        user_id=user_id,
        creation_key=creation_key,
        product_candidate_key=f"product-{setup_id}",
        product_version="v1",
        product_name=f"Товар {setup_id}",
        product_form="таблетки",
        product_dosage="10 мг",
        product_package="№20",
        product_manufacturer="Производитель",
        product_source_host="source.example",
        location_kind=LocationInputMode.CITY.value,
        location_key="city:moscow",
        location_display_name="Москва",
        location_city="Москва",
        radius_meters=5000,
        source_codes=["source-a"],
        notify_low_stock=False,
        notify_orderable=False,
        include_price=False,
        completion_mode=CompletionMode.CONTINUE.value,
        status=SubscriptionStatus.ACTIVE.value,
        availability_state=AvailabilityState.PENDING.value,
        has_partial_source_error=False,
        manual_check_in_progress=False,
        created_at=now,
        updated_at=now,
    )


async def test_postgres_list_enforces_ownership_and_manual_check_is_serialized(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    onboarding = SqlAlchemyOnboardingRepository(session_factory)
    repository = SqlAlchemySubscriptionRepository(session_factory)
    now = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)

    try:
        async with session_factory.begin() as session:
            await session.execute(delete(SubscriptionModel))
            await session.execute(delete(SubscriptionSetupDraftModel))
            await session.execute(delete(ConsentDecisionModel))
            await session.execute(delete(UserModel))
        owner = await onboarding.get_or_create_user(TelegramIdentity(801, 801))
        stranger = await onboarding.get_or_create_user(TelegramIdentity(802, 802))
        async with session_factory.begin() as session:
            own = model(owner.id, 101, "own-key", now)
            foreign = model(stranger.id, 102, "foreign-key", now)
            session.add_all([own, foreign])
        page = await repository.list_owned(
            owner.id,
            SubscriptionFilter.ALL,
            page=0,
            page_size=5,
        )
        assert len(page.items) == 1
        assert await repository.get_owned(owner.id, foreign.id) is None

        first = await repository.begin_manual_check(
            owner.id,
            own.id,
            now=now,
            cooldown=timedelta(minutes=5),
        )
        repeated = await repository.begin_manual_check(
            owner.id,
            own.id,
            now=now,
            cooldown=timedelta(minutes=5),
        )
        assert first.status is CheckGate.ACCEPTED
        assert repeated.status is CheckGate.IN_PROGRESS

        restored = await repository.mark_manual_check_failed(owner.id, own.id)
        limited = await repository.begin_manual_check(
            owner.id,
            own.id,
            now=now + timedelta(minutes=1),
            cooldown=timedelta(minutes=5),
        )
        assert restored and restored.availability_state is AvailabilityState.PENDING
        assert limited.status is CheckGate.RATE_LIMITED
    finally:
        await engine.dispose()
