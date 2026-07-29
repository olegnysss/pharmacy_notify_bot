from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.application.subscription_lifecycle import (
    LifecycleView,
    SubscriptionLifecycleService,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    SourceOption,
    SubscriptionStatus,
)
from pharmacy_bot.infrastructure.setup_capabilities import DemoLocationResolver
from pharmacy_bot.presentation.lifecycle_rendering import render_lifecycle
from tests.fakes import InMemoryOnboardingRepository
from tests.lifecycle_fakes import FixedValidator, InMemoryLifecycleRepository
from tests.subscription_fakes import subscription


@dataclass
class FixedClock:
    value: datetime = datetime(2026, 7, 29, 16, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class Sources:
    def __init__(self, *, supports_filters: bool = True) -> None:
        self.supports_filters = supports_filters

    async def available_sources(self, product, location) -> tuple[SourceOption, ...]:
        return (
            SourceOption(
                "source-a",
                "Аптека A",
                True,
                supports_price=self.supports_filters,
                supports_low_stock=self.supports_filters,
                supports_orderable=self.supports_filters,
            ),
        )


async def build(
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    valid: bool = True,
    supports_filters: bool = True,
) -> tuple[InMemoryLifecycleRepository, SubscriptionLifecycleService]:
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
    repository = InMemoryLifecycleRepository(subscription(1, status=status))
    service = SubscriptionLifecycleService(
        onboarding,
        repository,
        DemoLocationResolver(),
        Sources(supports_filters=supports_filters),
        FixedValidator(valid),
        draft_ttl=timedelta(hours=2),
        min_radius_meters=1000,
        max_radius_meters=25000,
        location_min_length=2,
        location_max_length=256,
        clock=FixedClock(),
    )
    return repository, service


def identity() -> TelegramIdentity:
    return TelegramIdentity(1001, 1001)


async def test_pause_and_resume_are_idempotent_and_preserve_configuration() -> None:
    repository, service = await build()
    original = repository.subscription

    paused = await service.pause(identity(), 1)
    repeated_pause = await service.pause(identity(), 1)
    resumed = await service.resume(identity(), 1)
    repeated_resume = await service.resume(identity(), 1)

    assert paused.view is LifecycleView.PAUSED
    assert repeated_pause.view is LifecycleView.PAUSED
    assert resumed.view is LifecycleView.RESUMED
    assert repeated_resume.view is LifecycleView.RESUMED
    assert repository.subscription.product == original.product
    assert repository.subscription.location == original.location
    assert repository.subscription.source_codes == original.source_codes
    assert [action for action, _ in repository.audit] == [
        "subscription_paused",
        "subscription_resumed",
    ]


async def test_resume_revalidates_configuration_before_transition() -> None:
    repository, service = await build(status=SubscriptionStatus.PAUSED, valid=False)

    result = await service.resume(identity(), 1)

    assert result.view is LifecycleView.INVALID_CONFIGURATION
    assert repository.subscription.status is SubscriptionStatus.PAUSED


async def test_repeated_resume_of_active_subscription_does_not_require_revalidation() -> None:
    repository, service = await build(valid=False)

    result = await service.resume(identity(), 1)

    assert result.view is LifecycleView.RESUMED
    assert repository.subscription.status is SubscriptionStatus.ACTIVE


async def test_cancelled_edit_does_not_change_original_subscription() -> None:
    repository, service = await build()
    original = repository.subscription
    started = await service.start_edit(identity(), 1)
    assert started.draft
    filters = await service.choose_block(identity(), started.draft.generation, 3)
    assert filters.draft
    changed = await service.toggle_filter(identity(), filters.draft.generation, 1)
    assert changed.draft

    cancelled = await service.cancel_edit(identity())

    assert cancelled and cancelled.view is LifecycleView.CANCELLED
    assert repository.subscription == original
    assert repository.audit == []


async def test_edit_shows_review_and_applies_once_without_changing_product() -> None:
    repository, service = await build()
    original_product = repository.subscription.product
    started = await service.start_edit(identity(), 1)
    assert started.draft
    completion = await service.choose_block(identity(), started.draft.generation, 4)
    assert completion.draft
    blocks = await service.choose_completion(
        identity(),
        completion.draft.generation,
        CompletionMode.PAUSE_AFTER_SUCCESS,
    )
    assert blocks.draft
    review = await service.choose_block(identity(), blocks.draft.generation, 5)
    assert review.draft
    rendered = render_lifecycle(review)

    first = await service.apply(identity(), review.draft.generation)
    second = await service.apply(identity(), review.draft.generation)

    assert first.view is LifecycleView.APPLIED
    assert second.view is LifecycleView.APPLIED
    assert repository.subscription.product == original_product
    assert repository.subscription.completion_mode is CompletionMode.PAUSE_AFTER_SUCCESS
    assert repository.audit == [("subscription_edited", 1)]
    assert "режим:" in rendered.text
    assert "Товар не заменяется" in rendered.text


async def test_edit_rejects_filter_unsupported_by_selected_sources() -> None:
    _, service = await build(supports_filters=False)
    started = await service.start_edit(identity(), 1)
    assert started.draft
    filters = await service.choose_block(identity(), started.draft.generation, 3)
    assert filters.draft

    result = await service.toggle_filter(identity(), filters.draft.generation, 1)

    assert result.view is LifecycleView.INPUT_ERROR
    assert result.error == "Выбранные источники не передают остатки."


async def test_delete_requires_current_confirmation_and_repeated_confirmation_is_safe() -> None:
    repository, service = await build()
    requested = await service.request_delete(identity(), 1)

    stale = await service.confirm_delete(identity(), 1, requested.version - 1)
    first = await service.confirm_delete(identity(), 1, requested.version)
    repeated = await service.confirm_delete(identity(), 1, requested.version)

    assert requested.view is LifecycleView.DELETE_CONFIRM
    assert stale.view is LifecycleView.STALE
    assert first.view is LifecycleView.DELETED
    assert repeated.view is LifecycleView.DELETED
    assert repository.subscription.status is SubscriptionStatus.DELETED
    assert repository.audit == [("subscription_deleted", 1)]
    rendered = render_lifecycle(requested)
    assert "Удаление необратимо" in rendered.text
    assert "Пауза" in rendered.text
