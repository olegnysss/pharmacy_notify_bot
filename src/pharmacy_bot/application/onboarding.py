from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pharmacy_bot.domain.onboarding import (
    ConsentMethod,
    OnboardingStatus,
    TelegramIdentity,
    UserSnapshot,
)


@dataclass(frozen=True, slots=True)
class DocumentBundle:
    terms_version: str
    terms_url: str
    privacy_version: str
    privacy_url: str


class OnboardingView(StrEnum):
    WELCOME = "welcome"
    CONSENT_REQUIRED = "consent_required"
    COMPLETED = "completed"
    DECLINED = "declined"
    MAIN_MENU = "main_menu"


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    view: OnboardingView
    user: UserSnapshot
    documents: DocumentBundle


class Clock(Protocol):
    def now(self) -> datetime: ...


class OnboardingRepository(Protocol):
    async def get_or_create_user(self, identity: TelegramIdentity) -> UserSnapshot: ...

    async def has_accepted(
        self,
        user_id: int,
        *,
        terms_version: str,
        privacy_version: str,
    ) -> bool: ...

    async def set_status(
        self,
        user_id: int,
        status: OnboardingStatus,
    ) -> UserSnapshot: ...

    async def accept(
        self,
        identity: TelegramIdentity,
        documents: DocumentBundle,
        *,
        accepted_at: datetime,
        method: ConsentMethod,
    ) -> UserSnapshot: ...

    async def decline(
        self,
        identity: TelegramIdentity,
        documents: DocumentBundle,
        *,
        declined_at: datetime,
        method: ConsentMethod,
    ) -> UserSnapshot: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class OnboardingService:
    def __init__(
        self,
        repository: OnboardingRepository,
        documents: DocumentBundle,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._documents = documents
        self._clock = clock or SystemClock()

    async def start(self, identity: TelegramIdentity) -> OnboardingResult:
        user = await self._repository.get_or_create_user(identity)
        if await self._has_current_consent(user.id):
            if user.status is not OnboardingStatus.COMPLETED:
                user = await self._repository.set_status(user.id, OnboardingStatus.COMPLETED)
            return self._result(OnboardingView.MAIN_MENU, user)

        if user.status is OnboardingStatus.NEW:
            user = await self._repository.set_status(
                user.id,
                OnboardingStatus.AWAITING_CONSENT,
            )
            return self._result(OnboardingView.WELCOME, user)

        if user.status is OnboardingStatus.COMPLETED:
            user = await self._repository.set_status(
                user.id,
                OnboardingStatus.AWAITING_CONSENT,
            )

        if user.status is OnboardingStatus.DECLINED:
            return self._result(OnboardingView.DECLINED, user)

        return self._result(OnboardingView.CONSENT_REQUIRED, user)

    async def continue_onboarding(self, identity: TelegramIdentity) -> OnboardingResult:
        user = await self._repository.get_or_create_user(identity)
        if await self._has_current_consent(user.id):
            if user.status is not OnboardingStatus.COMPLETED:
                user = await self._repository.set_status(user.id, OnboardingStatus.COMPLETED)
            return self._result(OnboardingView.MAIN_MENU, user)

        if user.status is not OnboardingStatus.AWAITING_CONSENT:
            user = await self._repository.set_status(
                user.id,
                OnboardingStatus.AWAITING_CONSENT,
            )
        return self._result(OnboardingView.CONSENT_REQUIRED, user)

    async def accept(self, identity: TelegramIdentity) -> OnboardingResult:
        user = await self._repository.accept(
            identity,
            self._documents,
            accepted_at=self._clock.now(),
            method=ConsentMethod.TELEGRAM_INLINE_BUTTON,
        )
        return self._result(OnboardingView.COMPLETED, user)

    async def decline(self, identity: TelegramIdentity) -> OnboardingResult:
        user = await self._repository.get_or_create_user(identity)
        if await self._has_current_consent(user.id):
            if user.status is not OnboardingStatus.COMPLETED:
                user = await self._repository.set_status(user.id, OnboardingStatus.COMPLETED)
            return self._result(OnboardingView.MAIN_MENU, user)

        user = await self._repository.decline(
            identity,
            self._documents,
            declined_at=self._clock.now(),
            method=ConsentMethod.TELEGRAM_INLINE_BUTTON,
        )
        return self._result(OnboardingView.DECLINED, user)

    async def _has_current_consent(self, user_id: int) -> bool:
        return await self._repository.has_accepted(
            user_id,
            terms_version=self._documents.terms_version,
            privacy_version=self._documents.privacy_version,
        )

    def _result(self, view: OnboardingView, user: UserSnapshot) -> OnboardingResult:
        return OnboardingResult(view=view, user=user, documents=self._documents)
