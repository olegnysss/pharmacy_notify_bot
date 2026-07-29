from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from pharmacy_bot.application.onboarding import DocumentBundle
from pharmacy_bot.domain.onboarding import (
    ConsentDecision,
    ConsentMethod,
    OnboardingStatus,
    TelegramIdentity,
    UserSnapshot,
)


class InMemoryOnboardingRepository:
    def __init__(self) -> None:
        self.users: dict[int, UserSnapshot] = {}
        self.decisions: set[tuple[int, str, str, ConsentDecision]] = set()
        self.decision_details: list[
            tuple[int, str, str, ConsentDecision, datetime, ConsentMethod]
        ] = []
        self._next_id = 1

    async def get_or_create_user(self, identity: TelegramIdentity) -> UserSnapshot:
        existing = self.users.get(identity.telegram_user_id)
        if existing is not None:
            updated = replace(existing, identity=identity)
            self.users[identity.telegram_user_id] = updated
            return updated

        user = UserSnapshot(
            id=self._next_id,
            identity=identity,
            status=OnboardingStatus.NEW,
        )
        self._next_id += 1
        self.users[identity.telegram_user_id] = user
        return user

    async def has_accepted(
        self,
        user_id: int,
        *,
        terms_version: str,
        privacy_version: str,
    ) -> bool:
        return (
            user_id,
            terms_version,
            privacy_version,
            ConsentDecision.ACCEPTED,
        ) in self.decisions

    async def set_status(
        self,
        user_id: int,
        status: OnboardingStatus,
    ) -> UserSnapshot:
        user = self._by_id(user_id)
        updated = replace(user, status=status)
        self.users[user.identity.telegram_user_id] = updated
        return updated

    async def accept(
        self,
        identity: TelegramIdentity,
        documents: DocumentBundle,
        *,
        accepted_at: datetime,
        method: ConsentMethod,
    ) -> UserSnapshot:
        user = await self.get_or_create_user(identity)
        key = (
            user.id,
            documents.terms_version,
            documents.privacy_version,
            ConsentDecision.ACCEPTED,
        )
        if key not in self.decisions:
            self.decisions.add(key)
            self.decision_details.append((*key, accepted_at, method))
        return await self.set_status(user.id, OnboardingStatus.COMPLETED)

    async def decline(
        self,
        identity: TelegramIdentity,
        documents: DocumentBundle,
        *,
        declined_at: datetime,
        method: ConsentMethod,
    ) -> UserSnapshot:
        user = await self.get_or_create_user(identity)
        if await self.has_accepted(
            user.id,
            terms_version=documents.terms_version,
            privacy_version=documents.privacy_version,
        ):
            return await self.set_status(user.id, OnboardingStatus.COMPLETED)

        key = (
            user.id,
            documents.terms_version,
            documents.privacy_version,
            ConsentDecision.DECLINED,
        )
        if key not in self.decisions:
            self.decisions.add(key)
            self.decision_details.append((*key, declined_at, method))
        return await self.set_status(user.id, OnboardingStatus.DECLINED)

    def _by_id(self, user_id: int) -> UserSnapshot:
        return next(user for user in self.users.values() if user.id == user_id)
