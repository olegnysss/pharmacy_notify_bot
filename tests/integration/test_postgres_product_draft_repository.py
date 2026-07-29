from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.product_selection import (
    DiscoveryResponse,
    DiscoveryStatus,
    MatchConfidence,
    ProductCandidate,
    ProductDraftStatus,
    ProductInputMode,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    ConsentDecisionModel,
    ProductSelectionCandidateModel,
    ProductSelectionDraftModel,
    UserModel,
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


async def test_postgres_draft_persists_versioned_candidates_and_rejects_stale_generation(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    onboarding_repository = SqlAlchemyOnboardingRepository(session_factory)
    repository = SqlAlchemyProductDraftRepository(session_factory)
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    try:
        async with session_factory.begin() as session:
            await session.execute(delete(ConsentDecisionModel))
            await session.execute(delete(UserModel))

        user = await onboarding_repository.get_or_create_user(
            TelegramIdentity(telegram_user_id=601, telegram_chat_id=601)
        )
        started = await repository.start_or_resume(
            user.id,
            now=now,
            expires_at=now + timedelta(hours=1),
        )
        awaiting = await repository.choose_input(
            user.id,
            ProductInputMode.SEARCH,
            expected_generation=started.generation,
            now=now,
            expires_at=now + timedelta(hours=1),
        )
        assert awaiting is not None
        searching = await repository.begin_discovery(
            user.id,
            query_text="товар 10 мг",
            source_host=None,
            now=now,
            expires_at=now + timedelta(hours=1),
        )
        assert searching is not None
        results = await repository.complete_discovery(
            user.id,
            generation=searching.generation,
            response=DiscoveryResponse(
                DiscoveryStatus.SUCCESS,
                (
                    ProductCandidate(
                        candidate_key="catalog-1",
                        version="card-v3",
                        name="Товар",
                        form="таблетки",
                        dosage="10 мг",
                        package="№20",
                        manufacturer="Производитель",
                        source_name="Аптека",
                        confidence=MatchConfidence.PROBABLE,
                    ),
                ),
            ),
            now=now,
        )
        assert results is not None
        stale = await repository.select_candidate(
            user.id,
            generation=searching.generation - 1,
            ordinal=0,
            now=now,
        )
        selected = await repository.select_candidate(
            user.id,
            generation=searching.generation,
            ordinal=0,
            now=now,
        )
        assert selected is not None
        confirmed = await repository.confirm_candidate(
            user.id,
            generation=searching.generation,
            ordinal=0,
            now=now,
        )

        restored = await SqlAlchemyProductDraftRepository(session_factory).get(user.id)
        async with session_factory() as session:
            drafts_count = await session.scalar(
                select(func.count()).select_from(ProductSelectionDraftModel)
            )
            candidates_count = await session.scalar(
                select(func.count()).select_from(ProductSelectionCandidateModel)
            )

        assert stale is None
        assert confirmed is not None
        assert confirmed.status is ProductDraftStatus.CONFIRMED
        assert restored is not None
        assert restored.selected_candidate is not None
        assert restored.selected_candidate.candidate_key == "catalog-1"
        assert restored.selected_candidate_version == "card-v3"
        assert drafts_count == 1
        assert candidates_count == 1

        cancelled = await repository.cancel(
            user.id,
            expected_generation=restored.generation,
            now=now,
        )
        assert cancelled is not None
        assert cancelled.status is ProductDraftStatus.CANCELLED
        assert cancelled.candidates == ()
    finally:
        await engine.dispose()
