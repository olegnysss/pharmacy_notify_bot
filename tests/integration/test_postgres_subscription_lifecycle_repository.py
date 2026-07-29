from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_lifecycle import EditStatus, LifecycleAction
from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    SourceOption,
    SubscriptionStatus,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    AuditLogModel,
    ConsentDecisionModel,
    SubscriptionEditDraftModel,
    SubscriptionModel,
    SubscriptionSetupDraftModel,
    UserModel,
)
from pharmacy_bot.infrastructure.onboarding_repository import (
    SqlAlchemyOnboardingRepository,
)
from pharmacy_bot.infrastructure.subscription_lifecycle_repository import (
    SqlAlchemySubscriptionLifecycleRepository,
)
from tests.integration.test_postgres_subscription_repository import model

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_postgres_lifecycle_transitions_are_idempotent_owned_and_audited(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    onboarding = SqlAlchemyOnboardingRepository(session_factory)
    repository = SqlAlchemySubscriptionLifecycleRepository(session_factory)
    now = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)

    try:
        async with session_factory.begin() as session:
            await session.execute(delete(AuditLogModel))
            await session.execute(delete(SubscriptionEditDraftModel))
            await session.execute(delete(SubscriptionModel))
            await session.execute(delete(SubscriptionSetupDraftModel))
            await session.execute(delete(ConsentDecisionModel))
            await session.execute(delete(UserModel))
        owner = await onboarding.get_or_create_user(TelegramIdentity(901, 901))
        stranger = await onboarding.get_or_create_user(TelegramIdentity(902, 902))
        async with session_factory.begin() as session:
            subscription_model = model(owner.id, 201, "lifecycle-key", now)
            session.add(subscription_model)
        subscription = await repository.get_owned_including_deleted(
            owner.id,
            subscription_model.id,
        )
        assert subscription is not None
        assert (
            await repository.get_owned_including_deleted(
                stranger.id,
                subscription_model.id,
            )
            is None
        )

        draft = await repository.start_edit(
            owner.id,
            subscription,
            (
                SourceOption(
                    "source-a",
                    "Аптека A",
                    True,
                    supports_low_stock=True,
                ),
            ),
            now=now,
            expires_at=now + timedelta(hours=2),
        )
        review = replace(
            draft,
            status=EditStatus.REVIEW,
            completion_mode=CompletionMode.PAUSE_AFTER_SUCCESS,
        )
        saved = await repository.save_edit(review, expected_generation=draft.generation)
        assert saved is not None
        applied = await repository.apply_edit(
            owner.id,
            expected_generation=saved.generation,
            now=now + timedelta(minutes=1),
        )
        repeated = await repository.apply_edit(
            owner.id,
            expected_generation=saved.generation,
            now=now + timedelta(minutes=1),
        )
        assert applied and repeated
        assert applied[1].subscription
        assert applied[1].subscription.completion_mode is CompletionMode.PAUSE_AFTER_SUCCESS

        paused = await repository.pause(
            owner.id,
            subscription_model.id,
            now=now + timedelta(minutes=2),
        )
        repeated_pause = await repository.pause(
            owner.id,
            subscription_model.id,
            now=now + timedelta(minutes=2),
        )
        resumed = await repository.resume(
            owner.id,
            subscription_model.id,
            now=now + timedelta(minutes=3),
        )
        assert paused.action is LifecycleAction.PAUSED
        assert repeated_pause.action is LifecycleAction.ALREADY_APPLIED
        assert resumed.action is LifecycleAction.RESUMED

        assert resumed.subscription and resumed.subscription.updated_at
        version = int(resumed.subscription.updated_at.timestamp())
        deleted = await repository.delete(
            owner.id,
            subscription_model.id,
            expected_version=version,
            now=now + timedelta(minutes=4),
        )
        repeated_delete = await repository.delete(
            owner.id,
            subscription_model.id,
            expected_version=version,
            now=now + timedelta(minutes=4),
        )
        async with session_factory() as session:
            audit_count = await session.scalar(select(func.count()).select_from(AuditLogModel))
        assert deleted.action is LifecycleAction.DELETED
        assert repeated_delete.action is LifecycleAction.ALREADY_APPLIED
        assert deleted.subscription
        assert deleted.subscription.status is SubscriptionStatus.DELETED
        assert audit_count == 4
    finally:
        await engine.dispose()
