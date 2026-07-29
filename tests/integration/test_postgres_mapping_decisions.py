from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.product_matching import (
    MappingActor,
    MappingConfirmation,
    MappingDecisionService,
    ProductMatchingEngine,
)
from pharmacy_bot.domain.product_matching import (
    MappingActorType,
    MappingAuthorizationError,
    MappingDecisionStatus,
    MappingScope,
    MatchIdentity,
    MatchRequest,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.mapping_decision_repository import (
    SqlAlchemyMappingDecisionRepository,
)
from pharmacy_bot.infrastructure.models import (
    CanonicalProductModel,
    MappingDecisionModel,
    SourceProductModel,
    UserModel,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_mapping_decision_is_scoped_idempotent_and_revocable(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyMappingDecisionRepository(session_factory)
    service = MappingDecisionService(repository)
    now = datetime(2026, 7, 29, 22, 0, tzinfo=UTC)
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(MappingDecisionModel))
            await session.execute(delete(SourceProductModel))
            await session.execute(delete(CanonicalProductModel))
            await session.execute(delete(UserModel))
            user = UserModel(
                telegram_user_id=900_001,
                telegram_chat_id=900_001,
                onboarding_status="active",
            )
            product = CanonicalProductModel(
                version=1,
                kind="medicine",
                quality="verified",
                critical_signature="a" * 64,
                trade_name_raw="Тест",
                trade_name_normalized="тест",
                form_raw="таблетки",
                form_normalized="таблетка",
                dosage_value=10,
                dosage_unit="mg",
                dosage_dimension="mass",
            )
            source = SourceProductModel(
                source_code="source-a",
                external_id="external-1",
                canonical_url="https://pharmacy.example/p/1",
                raw_name="Тест",
                parsed_attributes={"kind": "medicine"},
                status="active",
                semantic_fingerprint="b" * 64,
                search_document="тест",
                version=1,
                first_seen_at=now,
                last_seen_at=now,
                updated_at=now,
            )
            session.add_all((user, product, source))
            await session.flush()
            user_id, product_id, source_id = user.id, product.id, source.id

        source_identity = MatchIdentity(
            "medicine",
            "тест",
            form="таблетка",
            dosage="10 mg",
        )
        canonical_identity = MatchIdentity(
            "medicine",
            "тест",
            manufacturer="производитель",
            form="таблетка",
            dosage="10 mg",
        )
        request = MatchRequest(
            source_id,
            1,
            "source-a",
            source_identity,
            product_id,
            1,
            canonical_identity,
        )
        result = ProductMatchingEngine().evaluate(request)
        confirmation = MappingConfirmation(
            request,
            result,
            MappingActor(MappingActorType.USER, user_id),
            MappingScope.USER,
            user_id,
            "confirmed_in_product_picker",
            "telegram-update-123",
        )

        first = await service.confirm(confirmation, now=now)
        repeated = await service.confirm(confirmation, now=now + timedelta(seconds=1))
        allowed = await service.auto_event_allowed(request, result, user_id=user_id)
        not_allowed_for_other = await service.auto_event_allowed(
            request,
            result,
            user_id=user_id + 1,
        )
        revoked = await service.revoke(
            first.id,
            first.version,
            confirmation.actor,
            now=now + timedelta(minutes=1),
        )
        repeated_revoke = await service.revoke(
            first.id,
            first.version,
            confirmation.actor,
            now=now + timedelta(minutes=2),
        )
        allowed_after_revoke = await service.auto_event_allowed(
            request,
            result,
            user_id=user_id,
        )
        with pytest.raises(MappingAuthorizationError):
            await service.revoke(
                first.id,
                first.version,
                MappingActor(MappingActorType.USER, user_id + 1),
                now=now,
            )
        async with session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(MappingDecisionModel))

        assert first.id == repeated.id
        assert count == 1
        assert allowed
        assert not not_allowed_for_other
        assert revoked.status is MappingDecisionStatus.REVOKED
        assert revoked.version == 2
        assert repeated_revoke == revoked
        assert not allowed_after_revoke
    finally:
        await engine.dispose()
