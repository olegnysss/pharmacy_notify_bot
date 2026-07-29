from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pharmacy_bot.application.onboarding import OnboardingService, OnboardingView
from pharmacy_bot.domain.dialog import DialogRecovery, RecoveryState
from pharmacy_bot.domain.onboarding import TelegramIdentity


class RecoveryRepository(Protocol):
    async def inspect_and_cleanup(
        self,
        user_id: int,
        *,
        now: datetime,
        schema_version: int,
    ) -> DialogRecovery: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class DialogRecoveryService:
    SCHEMA_VERSION = 1

    def __init__(
        self,
        onboarding: OnboardingService,
        repository: RecoveryRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._onboarding = onboarding
        self._repository = repository
        self._clock = clock or SystemClock()

    async def inspect(self, identity: TelegramIdentity) -> DialogRecovery:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return DialogRecovery(RecoveryState.NONE)
        return await self._repository.inspect_and_cleanup(
            onboarding.user.id,
            now=self._clock.now(),
            schema_version=self.SCHEMA_VERSION,
        )
