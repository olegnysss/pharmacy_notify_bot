from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.application.subscription_setup import (
    LocationResolution,
    SetupView,
    SubscriptionSetupService,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    SourceOption,
)
from tests.fakes import InMemoryOnboardingRepository
from tests.setup_fakes import ConfirmedProductDraftReader, InMemorySetupRepository
from tests.user_settings_fakes import InMemorySettingsRepository


@dataclass
class FixedClock:
    value: datetime = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class FakeLocations:
    async def resolve(
        self,
        mode: LocationInputMode,
        text: str,
    ) -> LocationResolution:
        if text == "ошибка":
            return LocationResolution(temporary_error=True)
        if mode is LocationInputMode.ADDRESS:
            return LocationResolution(
                (
                    LocationCandidate(
                        "address-1",
                        mode,
                        f"{text}, строение 1",
                        address=f"{text}, строение 1",
                        confidence=LocationConfidence.AMBIGUOUS,
                    ),
                    LocationCandidate(
                        "address-2",
                        mode,
                        f"{text}, строение 2",
                        address=f"{text}, строение 2",
                        confidence=LocationConfidence.AMBIGUOUS,
                    ),
                )
            )
        return LocationResolution(
            (
                LocationCandidate(
                    f"city:{text}",
                    mode,
                    text,
                    city=text,
                    confidence=LocationConfidence.EXACT,
                ),
            )
        )


class FakeSources:
    def __init__(self, options: tuple[SourceOption, ...] | None = None) -> None:
        self.options = (
            options
            if options is not None
            else (
                SourceOption(
                    "source-a",
                    "Аптека A",
                    True,
                    supports_price=True,
                    supports_low_stock=True,
                    supports_orderable=True,
                ),
                SourceOption(
                    "source-b",
                    "Аптека B",
                    False,
                    unavailable_reason="На обслуживании",
                ),
            )
        )

    async def available_sources(self, product, location) -> tuple[SourceOption, ...]:
        return self.options


def identity() -> TelegramIdentity:
    return TelegramIdentity(telegram_user_id=1001, telegram_chat_id=1001)


async def build_service(
    *,
    sources: FakeSources | None = None,
    preferences: InMemorySettingsRepository | None = None,
) -> tuple[OnboardingService, InMemorySetupRepository, SubscriptionSetupService]:
    onboarding = OnboardingService(
        InMemoryOnboardingRepository(),
        DocumentBundle(
            "terms-v1",
            "https://example.com/terms",
            "privacy-v1",
            "https://example.com/privacy",
        ),
    )
    await onboarding.accept(identity())
    repository = InMemorySetupRepository()
    return (
        onboarding,
        repository,
        SubscriptionSetupService(
            onboarding,
            ConfirmedProductDraftReader(),
            repository,
            FakeLocations(),
            sources or FakeSources(),
            draft_ttl=timedelta(hours=2),
            location_min_length=2,
            location_max_length=256,
            min_radius_meters=1000,
            max_radius_meters=25000,
            preferences=preferences,
            max_active_subscriptions=2,
            max_sources_per_subscription=1,
            clock=FixedClock(),
        ),
    )


async def reach_sources(
    service: SubscriptionSetupService,
) -> tuple[int, object]:
    started = await service.start(identity())
    assert started.draft
    await service.choose_location_mode(
        identity(),
        LocationInputMode.CITY,
        started.draft.generation,
    )
    chosen = await service.submit_text(identity(), "Москва")
    assert chosen and chosen.draft
    sources = await service.set_radius(identity(), chosen.draft.generation, 5000)
    assert sources.draft
    return sources.draft.generation, sources


async def test_full_rule_is_visible_and_double_confirmation_creates_one_subscription() -> None:
    _, repository, service = await build_service()
    generation, sources_result = await reach_sources(service)
    assert sources_result.view is SetupView.CHOOSE_SOURCES
    assert sources_result.draft
    assert sources_result.draft.selected_source_codes == ("source-a",)

    filters = await service.confirm_sources(identity(), generation)
    assert filters.draft
    filters = await service.toggle_filter(identity(), filters.draft.generation, 1)
    assert filters.draft
    completion = await service.confirm_filters(identity(), filters.draft.generation)
    assert completion.draft
    review = await service.choose_completion(
        identity(),
        completion.draft.generation,
        CompletionMode.CONTINUE,
    )
    assert review.view is SetupView.REVIEW
    assert review.draft

    first = await service.confirm(identity(), review.draft.generation)
    second = await service.confirm(identity(), review.draft.generation)

    assert first.view is SetupView.CREATED
    assert second.view is SetupView.CREATED
    assert first.subscription == second.subscription
    assert first.subscription
    assert first.subscription.availability_state is AvailabilityState.PENDING
    assert repository.creation_count == 1

    next_setup = await service.start(identity())

    assert next_setup.view is SetupView.CHOOSE_LOCATION
    assert next_setup.draft
    assert next_setup.draft.idempotency_key != review.draft.idempotency_key


async def test_ambiguous_address_requires_explicit_candidate_confirmation() -> None:
    _, _, service = await build_service()
    started = await service.start(identity())
    assert started.draft
    awaiting = await service.choose_location_mode(
        identity(),
        LocationInputMode.ADDRESS,
        started.draft.generation,
    )
    assert awaiting.draft

    candidates = await service.submit_text(identity(), "Ленина 1")

    assert candidates
    assert candidates.view is SetupView.LOCATION_RESULTS
    assert candidates.draft
    assert candidates.draft.location is None
    selected = await service.select_location(
        identity(),
        candidates.draft.generation,
        1,
    )
    assert selected.view is SetupView.CHOOSE_RADIUS
    assert selected.draft and selected.draft.location
    assert selected.draft.location.key == "address-2"


async def test_empty_or_disabled_source_cannot_form_monitoring_scope() -> None:
    _, _, service = await build_service(sources=FakeSources(options=()))
    generation, result = await reach_sources(service)

    blocked = await service.confirm_sources(identity(), generation)

    assert result.draft and result.draft.selected_source_codes == ()
    assert blocked.view is SetupView.INPUT_ERROR
    assert "хотя бы один" in (blocked.error or "")


async def test_invalid_radius_and_old_generation_do_not_change_draft() -> None:
    _, repository, service = await build_service()
    started = await service.start(identity())
    assert started.draft
    awaiting = await service.choose_location_mode(
        identity(),
        LocationInputMode.CITY,
        started.draft.generation,
    )
    assert awaiting.draft
    location = await service.submit_text(identity(), "Казань")
    assert location and location.draft

    invalid = await service.set_radius(identity(), location.draft.generation, 500)
    stale = await service.set_radius(identity(), location.draft.generation - 1, 5000)

    assert invalid.view is SetupView.INPUT_ERROR
    assert stale.view is SetupView.STALE
    current = repository.drafts[location.draft.user_id]
    assert current.radius_meters is None


async def test_until_date_is_validated_in_user_timezone_and_saved_in_utc() -> None:
    _, _, service = await build_service()
    generation, _ = await reach_sources(service)
    filters = await service.confirm_sources(identity(), generation)
    assert filters.draft
    completion = await service.confirm_filters(identity(), filters.draft.generation)
    assert completion.draft
    awaiting_date = await service.choose_completion(
        identity(),
        completion.draft.generation,
        CompletionMode.UNTIL_DATE,
    )
    assert awaiting_date.draft

    invalid = await service.submit_text(identity(), "28.07.2026")
    valid = await service.submit_text(identity(), "30.07.2026")

    assert invalid and invalid.view is SetupView.INPUT_ERROR
    assert valid and valid.view is SetupView.REVIEW
    assert valid.draft and valid.draft.ends_at
    assert valid.draft.ends_at.tzinfo is UTC


async def test_new_setup_applies_visible_defaults_without_changing_existing_rules() -> None:
    preferences = InMemorySettingsRepository()
    value = await preferences.get_or_create(1)
    location = LocationCandidate(
        "city:moscow",
        LocationInputMode.CITY,
        "Москва",
        city="Москва",
        confidence=LocationConfidence.EXACT,
    )
    preferences.value = replace(
        value,
        default_location=location,
        default_radius_meters=5000,
        default_source_codes=("source-a",),
        completion_mode=CompletionMode.PAUSE_AFTER_SUCCESS,
    )
    _, _, service = await build_service(preferences=preferences)

    result = await service.start(identity())

    assert result.view is SetupView.REVIEW
    assert result.draft
    assert result.draft.location == location
    assert result.draft.selected_source_codes == ("source-a",)
    assert result.draft.completion_mode is CompletionMode.PAUSE_AFTER_SUCCESS


async def test_known_subscription_quota_blocks_early_with_recovery_action() -> None:
    preferences = InMemorySettingsRepository(active_subscriptions=2)
    _, repository, service = await build_service(preferences=preferences)

    result = await service.start(identity())

    assert result.view is SetupView.QUOTA_EXCEEDED
    assert "Приостановите или удалите" in (result.error or "")
    assert repository.drafts == {}
