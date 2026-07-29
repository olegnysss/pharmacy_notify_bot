from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.onboarding import DocumentBundle
from pharmacy_bot.domain.onboarding import (
    ConsentDecision,
    ConsentMethod,
    OnboardingStatus,
    TelegramIdentity,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import ConsentDecisionModel, UserModel
from pharmacy_bot.infrastructure.onboarding_repository import (
    SqlAlchemyOnboardingRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_postgres_repository_persists_decisions_idempotently(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyOnboardingRepository(session_factory)
    identity = TelegramIdentity(telegram_user_id=501, telegram_chat_id=501, language_code="ru")
    documents = DocumentBundle(
        terms_version="terms-integration-v1",
        terms_url="https://example.com/terms",
        privacy_version="privacy-integration-v1",
        privacy_url="https://example.com/privacy",
    )
    occurred_at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    try:
        async with session_factory.begin() as session:
            await session.execute(delete(ConsentDecisionModel))
            await session.execute(delete(UserModel))

        user = await repository.get_or_create_user(identity)
        declined = await repository.decline(
            identity,
            documents,
            declined_at=occurred_at,
            method=ConsentMethod.TELEGRAM_INLINE_BUTTON,
        )
        first_accept = await repository.accept(
            identity,
            documents,
            accepted_at=occurred_at,
            method=ConsentMethod.TELEGRAM_INLINE_BUTTON,
        )
        second_accept = await repository.accept(
            identity,
            documents,
            accepted_at=occurred_at,
            method=ConsentMethod.TELEGRAM_INLINE_BUTTON,
        )

        async with session_factory() as session:
            users_count = await session.scalar(select(func.count()).select_from(UserModel))
            decisions = (
                await session.scalars(
                    select(ConsentDecisionModel.decision).order_by(ConsentDecisionModel.decision)
                )
            ).all()

        assert user.id == declined.id == first_accept.id == second_accept.id
        assert declined.status is OnboardingStatus.DECLINED
        assert first_accept.status is OnboardingStatus.COMPLETED
        assert second_accept.status is OnboardingStatus.COMPLETED
        assert users_count == 1
        assert decisions == [
            ConsentDecision.ACCEPTED.value,
            ConsentDecision.DECLINED.value,
        ]
    finally:
        await engine.dispose()
