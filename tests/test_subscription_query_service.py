from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.application.subscriptions import (
    CheckEnqueueResult,
    CheckGate,
    SubscriptionFilter,
    SubscriptionQueryService,
    SubscriptionView,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import SubscriptionStatus
from tests.fakes import InMemoryOnboardingRepository
from tests.subscription_fakes import (
    FakeScheduler,
    InMemorySubscriptionRepository,
    subscription,
)


@dataclass
class FixedClock:
    value: datetime = datetime(2026, 7, 29, 14, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


async def service(
    repository: InMemorySubscriptionRepository,
    scheduler: FakeScheduler | None = None,
) -> SubscriptionQueryService:
    onboarding = OnboardingService(
        InMemoryOnboardingRepository(),
        DocumentBundle(
            "terms-v1",
            "https://example.com/terms",
            "privacy-v1",
            "https://example.com/privacy",
        ),
    )
    await onboarding.accept(TelegramIdentity(1001, 1001))
    return SubscriptionQueryService(
        onboarding,
        repository,
        scheduler or FakeScheduler(),
        page_size=2,
        manual_check_cooldown=timedelta(minutes=5),
        clock=FixedClock(),
    )


def identity() -> TelegramIdentity:
    return TelegramIdentity(1001, 1001)


async def test_list_filters_and_pages_only_owned_subscriptions() -> None:
    repository = InMemorySubscriptionRepository(
        (
            subscription(1),
            subscription(2, status=SubscriptionStatus.PAUSED),
            subscription(3),
            subscription(4, user_id=999),
        )
    )
    query = await service(repository)

    first = await query.list(identity())
    second = await query.list(identity(), page=1)
    paused = await query.list(
        identity(),
        selected_filter=SubscriptionFilter.PAUSED,
    )

    assert first.page and [item.id for item in first.page.items] == [3, 2]
    assert second.page and [item.id for item in second.page.items] == [1]
    assert paused.page and [item.id for item in paused.page.items] == [2]


async def test_changed_list_version_rejects_stale_pagination_callback() -> None:
    query = await service(InMemorySubscriptionRepository((subscription(1),)))

    result = await query.list(identity(), expected_version=41)

    assert result.view is SubscriptionView.STALE


async def test_foreign_subscription_is_indistinguishable_from_missing() -> None:
    query = await service(InMemorySubscriptionRepository((subscription(5, user_id=999),)))

    result = await query.details(identity(), 5)

    assert result.view is SubscriptionView.NOT_FOUND
    assert result.subscription is None


async def test_manual_check_queues_once_and_scheduler_failure_preserves_state() -> None:
    repository = InMemorySubscriptionRepository((subscription(1),))
    scheduler = FakeScheduler(CheckEnqueueResult.TEMPORARY_ERROR)
    query = await service(repository, scheduler)

    result = await query.check_now(identity(), 1)

    assert result.view is SubscriptionView.CHECK_ERROR
    assert result.subscription
    assert result.subscription.manual_check_in_progress is False
    assert scheduler.calls == 1


async def test_cache_in_progress_and_rate_limit_do_not_enqueue_external_work() -> None:
    repository = InMemorySubscriptionRepository((subscription(1),))
    scheduler = FakeScheduler()
    query = await service(repository, scheduler)

    for gate, expected in (
        (CheckGate.CACHED, SubscriptionView.CHECK_CACHED),
        (CheckGate.IN_PROGRESS, SubscriptionView.CHECK_IN_PROGRESS),
        (CheckGate.RATE_LIMITED, SubscriptionView.CHECK_RATE_LIMITED),
    ):
        repository.gate = gate
        result = await query.check_now(identity(), 1)
        assert result.view is expected

    assert scheduler.calls == 0
