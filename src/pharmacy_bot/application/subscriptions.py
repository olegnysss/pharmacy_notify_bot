from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pharmacy_bot.application.onboarding import OnboardingResult, OnboardingService, OnboardingView
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import Subscription


class SubscriptionFilter(StrEnum):
    ALL = "all"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class SubscriptionView(StrEnum):
    ONBOARDING = "onboarding"
    LIST = "list"
    DETAILS = "details"
    NOT_FOUND = "not_found"
    STALE = "stale"
    CHECK_CACHED = "check_cached"
    CHECK_QUEUED = "check_queued"
    CHECK_IN_PROGRESS = "check_in_progress"
    CHECK_RATE_LIMITED = "check_rate_limited"
    CHECK_ERROR = "check_error"
    ACTION_UNAVAILABLE = "action_unavailable"


class CheckGate(StrEnum):
    CACHED = "cached"
    IN_PROGRESS = "in_progress"
    RATE_LIMITED = "rate_limited"
    ACCEPTED = "accepted"
    NOT_FOUND = "not_found"
    NOT_ACTIVE = "not_active"


class CheckEnqueueResult(StrEnum):
    QUEUED = "queued"
    TEMPORARY_ERROR = "temporary_error"


@dataclass(frozen=True, slots=True)
class SubscriptionPage:
    items: tuple[Subscription, ...]
    page: int
    total_pages: int
    total_items: int
    version: int


@dataclass(frozen=True, slots=True)
class CheckGateResult:
    status: CheckGate
    subscription: Subscription | None = None
    retry_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionResult:
    view: SubscriptionView
    onboarding: OnboardingResult
    subscription: Subscription | None = None
    page: SubscriptionPage | None = None
    selected_filter: SubscriptionFilter = SubscriptionFilter.ALL
    retry_at: datetime | None = None


class SubscriptionRepository(Protocol):
    async def list_owned(
        self,
        user_id: int,
        selected_filter: SubscriptionFilter,
        *,
        page: int,
        page_size: int,
    ) -> SubscriptionPage: ...

    async def get_owned(self, user_id: int, subscription_id: int) -> Subscription | None: ...

    async def begin_manual_check(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
        cooldown: timedelta,
    ) -> CheckGateResult: ...

    async def mark_manual_check_failed(
        self,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None: ...


class ManualCheckScheduler(Protocol):
    async def enqueue(self, subscription: Subscription) -> CheckEnqueueResult: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SubscriptionQueryService:
    def __init__(
        self,
        onboarding: OnboardingService,
        repository: SubscriptionRepository,
        scheduler: ManualCheckScheduler,
        *,
        page_size: int,
        manual_check_cooldown: timedelta,
        clock: Clock | None = None,
    ) -> None:
        self._onboarding = onboarding
        self._repository = repository
        self._scheduler = scheduler
        self._page_size = page_size
        self._cooldown = manual_check_cooldown
        self._clock = clock or SystemClock()

    async def list(
        self,
        identity: TelegramIdentity,
        *,
        selected_filter: SubscriptionFilter = SubscriptionFilter.ALL,
        page: int = 0,
        expected_version: int | None = None,
    ) -> SubscriptionResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return SubscriptionResult(SubscriptionView.ONBOARDING, onboarding)
        result = await self._repository.list_owned(
            onboarding.user.id,
            selected_filter,
            page=page,
            page_size=self._page_size,
        )
        if expected_version is not None and expected_version != result.version:
            return SubscriptionResult(
                SubscriptionView.STALE,
                onboarding,
                page=result,
                selected_filter=selected_filter,
            )
        return SubscriptionResult(
            SubscriptionView.LIST,
            onboarding,
            page=result,
            selected_filter=selected_filter,
        )

    async def details(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> SubscriptionResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return SubscriptionResult(SubscriptionView.ONBOARDING, onboarding)
        subscription = await self._repository.get_owned(
            onboarding.user.id,
            subscription_id,
        )
        return SubscriptionResult(
            SubscriptionView.DETAILS if subscription else SubscriptionView.NOT_FOUND,
            onboarding,
            subscription=subscription,
        )

    async def check_now(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> SubscriptionResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return SubscriptionResult(SubscriptionView.ONBOARDING, onboarding)
        gate = await self._repository.begin_manual_check(
            onboarding.user.id,
            subscription_id,
            now=self._clock.now(),
            cooldown=self._cooldown,
        )
        views = {
            CheckGate.CACHED: SubscriptionView.CHECK_CACHED,
            CheckGate.IN_PROGRESS: SubscriptionView.CHECK_IN_PROGRESS,
            CheckGate.RATE_LIMITED: SubscriptionView.CHECK_RATE_LIMITED,
            CheckGate.NOT_FOUND: SubscriptionView.NOT_FOUND,
            CheckGate.NOT_ACTIVE: SubscriptionView.CHECK_ERROR,
        }
        if gate.status is not CheckGate.ACCEPTED:
            return SubscriptionResult(
                views[gate.status],
                onboarding,
                subscription=gate.subscription,
                retry_at=gate.retry_at,
            )
        if gate.subscription is None:
            return SubscriptionResult(SubscriptionView.NOT_FOUND, onboarding)
        enqueue = await self._scheduler.enqueue(gate.subscription)
        if enqueue is CheckEnqueueResult.TEMPORARY_ERROR:
            restored = await self._repository.mark_manual_check_failed(
                onboarding.user.id,
                subscription_id,
            )
            return SubscriptionResult(
                SubscriptionView.CHECK_ERROR,
                onboarding,
                subscription=restored or gate.subscription,
            )
        return SubscriptionResult(
            SubscriptionView.CHECK_QUEUED,
            onboarding,
            subscription=gate.subscription,
        )

    async def future_action(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> SubscriptionResult:
        result = await self.details(identity, subscription_id)
        if result.view is not SubscriptionView.DETAILS:
            return result
        return SubscriptionResult(
            SubscriptionView.ACTION_UNAVAILABLE,
            result.onboarding,
            subscription=result.subscription,
        )
