from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from pharmacy_bot.domain.dialog import DialogScenario, RecoveryState, UpdateClaim
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.product_selection import ProductDraftStatus
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.dialog_repository import (
    SqlAlchemyDialogRecoveryRepository,
    SqlAlchemyUpdateReceiptRepository,
)
from pharmacy_bot.infrastructure.models import (
    AuditLogModel,
    ConsentDecisionModel,
    ProductSelectionDraftModel,
    SubscriptionEditDraftModel,
    SubscriptionModel,
    SubscriptionSetupDraftModel,
    TelegramUpdateReceiptModel,
    UserModel,
    UserPreferencesModel,
)
from pharmacy_bot.infrastructure.onboarding_repository import (
    SqlAlchemyOnboardingRepository,
)
from pharmacy_bot.infrastructure.product_draft_repository import (
    SqlAlchemyProductDraftRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_update_receipts_deduplicate_and_retry_failed_claims(database_url: str) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyUpdateReceiptRepository(session_factory)
    now = datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(TelegramUpdateReceiptModel))

        first = await repository.claim(100, now=now, lease=timedelta(minutes=2))
        concurrent = await repository.claim(100, now=now, lease=timedelta(minutes=2))
        await repository.complete(100, now=now + timedelta(seconds=1))
        completed = await repository.claim(
            100,
            now=now + timedelta(seconds=2),
            lease=timedelta(minutes=2),
        )
        failed_first = await repository.claim(101, now=now, lease=timedelta(minutes=2))
        await repository.fail(
            101,
            now=now + timedelta(seconds=1),
            correlation_id="11111111-1111-1111-1111-111111111111",
        )
        retry = await repository.claim(
            101,
            now=now + timedelta(seconds=2),
            lease=timedelta(minutes=2),
        )
        async with session_factory() as session:
            failed_receipt = await session.get(TelegramUpdateReceiptModel, 101)

        assert first is UpdateClaim.CLAIMED
        assert concurrent is UpdateClaim.IN_PROGRESS
        assert completed is UpdateClaim.COMPLETED
        assert failed_first is UpdateClaim.CLAIMED
        assert retry is UpdateClaim.CLAIMED
        assert failed_receipt and failed_receipt.attempts == 2
        assert failed_receipt.last_error_id is None
    finally:
        await engine.dispose()


async def test_recovery_resumes_current_draft_and_resets_expired_state(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    onboarding = SqlAlchemyOnboardingRepository(session_factory)
    products = SqlAlchemyProductDraftRepository(session_factory)
    recovery = SqlAlchemyDialogRecoveryRepository(session_factory)
    now = datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(AuditLogModel))
            await session.execute(delete(SubscriptionEditDraftModel))
            await session.execute(delete(SubscriptionModel))
            await session.execute(delete(SubscriptionSetupDraftModel))
            await session.execute(delete(ProductSelectionDraftModel))
            await session.execute(delete(UserPreferencesModel))
            await session.execute(delete(ConsentDecisionModel))
            await session.execute(delete(UserModel))
        user = await onboarding.get_or_create_user(TelegramIdentity(940, 940))
        await products.start_or_resume(
            user.id,
            now=now,
            expires_at=now + timedelta(hours=1),
        )

        active = await recovery.inspect_and_cleanup(
            user.id,
            now=now,
            schema_version=1,
        )
        async with session_factory.begin() as session:
            await session.execute(
                update(ProductSelectionDraftModel)
                .where(ProductSelectionDraftModel.user_id == user.id)
                .values(schema_version=99)
            )
        reset_schema = await recovery.inspect_and_cleanup(
            user.id,
            now=now,
            schema_version=1,
        )
        await products.start_or_resume(
            user.id,
            now=now,
            expires_at=now + timedelta(hours=1),
        )
        async with session_factory.begin() as session:
            resumed_schema = await session.scalar(
                select(ProductSelectionDraftModel.schema_version).where(
                    ProductSelectionDraftModel.user_id == user.id
                )
            )
            await session.execute(
                update(ProductSelectionDraftModel)
                .where(ProductSelectionDraftModel.user_id == user.id)
                .values(expires_at=now - timedelta(seconds=1))
            )
        reset_expired = await recovery.inspect_and_cleanup(
            user.id,
            now=now,
            schema_version=1,
        )
        async with session_factory() as session:
            stored_status = await session.scalar(
                select(ProductSelectionDraftModel.status).where(
                    ProductSelectionDraftModel.user_id == user.id
                )
            )

        assert active.state is RecoveryState.ACTIVE
        assert active.scenario is DialogScenario.PRODUCT_SELECTION
        assert reset_schema.state is RecoveryState.RESET
        assert resumed_schema == 1
        assert reset_expired.state is RecoveryState.RESET
        assert stored_status == ProductDraftStatus.CANCELLED.value
    finally:
        await engine.dispose()
