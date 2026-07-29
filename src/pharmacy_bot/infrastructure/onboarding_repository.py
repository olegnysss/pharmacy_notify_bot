from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.onboarding import DocumentBundle
from pharmacy_bot.domain.onboarding import (
    ConsentDecision,
    ConsentMethod,
    OnboardingStatus,
    TelegramIdentity,
    UserSnapshot,
)
from pharmacy_bot.infrastructure.models import ConsentDecisionModel, UserModel


class SqlAlchemyOnboardingRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create_user(self, identity: TelegramIdentity) -> UserSnapshot:
        async with self._session_factory.begin() as session:
            model = await self._upsert_user(session, identity)
            return self._snapshot(model)

    async def has_accepted(
        self,
        user_id: int,
        *,
        terms_version: str,
        privacy_version: str,
    ) -> bool:
        async with self._session_factory() as session:
            statement = select(ConsentDecisionModel.id).where(
                ConsentDecisionModel.user_id == user_id,
                ConsentDecisionModel.terms_version == terms_version,
                ConsentDecisionModel.privacy_version == privacy_version,
                ConsentDecisionModel.decision == ConsentDecision.ACCEPTED.value,
            )
            return (await session.scalar(statement)) is not None

    async def set_status(
        self,
        user_id: int,
        status: OnboardingStatus,
    ) -> UserSnapshot:
        async with self._session_factory.begin() as session:
            statement = (
                update(UserModel)
                .where(UserModel.id == user_id)
                .values(onboarding_status=status.value)
                .returning(UserModel)
            )
            model = (await session.scalars(statement)).one()
            return self._snapshot(model)

    async def accept(
        self,
        identity: TelegramIdentity,
        documents: DocumentBundle,
        *,
        accepted_at: datetime,
        method: ConsentMethod,
    ) -> UserSnapshot:
        async with self._session_factory.begin() as session:
            user = await self._upsert_user(session, identity)
            consent_statement = (
                insert(ConsentDecisionModel)
                .values(
                    user_id=user.id,
                    terms_version=documents.terms_version,
                    privacy_version=documents.privacy_version,
                    decision=ConsentDecision.ACCEPTED.value,
                    occurred_at=accepted_at,
                    method=method.value,
                )
                .on_conflict_do_nothing(
                    constraint="uq_consent_decisions_user_versions_decision",
                )
            )
            await session.execute(consent_statement)
            user.onboarding_status = OnboardingStatus.COMPLETED.value
            await session.flush()
            return self._snapshot(user)

    async def decline(
        self,
        identity: TelegramIdentity,
        documents: DocumentBundle,
        *,
        declined_at: datetime,
        method: ConsentMethod,
    ) -> UserSnapshot:
        async with self._session_factory.begin() as session:
            user = await self._upsert_user(session, identity)
            accepted_statement = select(ConsentDecisionModel.id).where(
                ConsentDecisionModel.user_id == user.id,
                ConsentDecisionModel.terms_version == documents.terms_version,
                ConsentDecisionModel.privacy_version == documents.privacy_version,
                ConsentDecisionModel.decision == ConsentDecision.ACCEPTED.value,
            )
            accepted = (await session.scalar(accepted_statement)) is not None
            if not accepted:
                decision_statement = (
                    insert(ConsentDecisionModel)
                    .values(
                        user_id=user.id,
                        terms_version=documents.terms_version,
                        privacy_version=documents.privacy_version,
                        decision=ConsentDecision.DECLINED.value,
                        occurred_at=declined_at,
                        method=method.value,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_consent_decisions_user_versions_decision",
                    )
                )
                await session.execute(decision_statement)
            user.onboarding_status = (
                OnboardingStatus.COMPLETED.value if accepted else OnboardingStatus.DECLINED.value
            )
            await session.flush()
            return self._snapshot(user)

    async def _upsert_user(
        self,
        session: AsyncSession,
        identity: TelegramIdentity,
    ) -> UserModel:
        statement = (
            insert(UserModel)
            .values(
                telegram_user_id=identity.telegram_user_id,
                telegram_chat_id=identity.telegram_chat_id,
                language_code=identity.language_code,
                onboarding_status=OnboardingStatus.NEW.value,
            )
            .on_conflict_do_update(
                index_elements=[UserModel.telegram_user_id],
                set_={
                    "telegram_chat_id": identity.telegram_chat_id,
                    "language_code": identity.language_code,
                },
            )
            .returning(UserModel)
        )
        return (await session.scalars(statement)).one()

    @staticmethod
    def _snapshot(model: UserModel) -> UserSnapshot:
        return UserSnapshot(
            id=model.id,
            identity=TelegramIdentity(
                telegram_user_id=model.telegram_user_id,
                telegram_chat_id=model.telegram_chat_id,
                language_code=model.language_code,
            ),
            status=OnboardingStatus(model.onboarding_status),
        )
