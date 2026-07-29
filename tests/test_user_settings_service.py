from __future__ import annotations

from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.application.user_settings import (
    SettingsView,
    UserSettingsService,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import LocationInputMode, SourceOption
from pharmacy_bot.domain.user_settings import ServiceLimits
from pharmacy_bot.infrastructure.setup_capabilities import DemoLocationResolver
from pharmacy_bot.presentation.user_settings_rendering import render_user_settings
from tests.fakes import InMemoryOnboardingRepository
from tests.user_settings_fakes import InMemorySettingsRepository


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
            SourceOption("source-b", "Аптека B", False, "На обслуживании"),
        )


async def build(
    *,
    supports_filters: bool = True,
) -> tuple[InMemorySettingsRepository, UserSettingsService]:
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
    repository = InMemorySettingsRepository()
    service = UserSettingsService(
        onboarding,
        repository,
        DemoLocationResolver(),
        Sources(supports_filters=supports_filters),
        limits(),
    )
    return repository, service


def identity() -> TelegramIdentity:
    return TelegramIdentity(1001, 1001)


def limits() -> ServiceLimits:
    return ServiceLimits(
        min_radius_meters=1000,
        max_radius_meters=25000,
        max_sources_per_subscription=1,
        max_active_subscriptions=2,
        manual_check_cooldown_seconds=300,
        location_min_length=2,
        location_max_length=256,
        product_query_min_length=2,
        product_query_max_length=160,
    )


async def test_location_defaults_require_confirmation_and_can_be_cleared() -> None:
    repository, service = await build()
    opened = await service.open(identity(), location_only=True)
    assert opened.preferences
    awaiting = await service.choose_location_mode(
        identity(),
        opened.preferences.generation,
        LocationInputMode.ADDRESS,
    )
    assert awaiting.view is SettingsView.AWAITING_LOCATION

    candidates = await service.submit_text(identity(), "Тверская 1")
    assert candidates and candidates.preferences
    assert candidates.view is SettingsView.LOCATION_RESULTS
    radius = await service.select_location(
        identity(),
        candidates.preferences.generation,
        0,
    )
    assert radius.preferences
    sources = await service.set_radius(identity(), radius.preferences.generation, 5000)
    assert sources.preferences
    selected = await service.toggle_source(
        identity(),
        sources.preferences.generation,
        0,
    )
    assert selected.preferences
    saved = await service.finish_sources(identity(), selected.preferences.generation)

    assert saved.view is SettingsView.SAVED
    assert repository.value
    assert repository.value.default_location
    assert repository.value.default_radius_meters == 5000
    assert repository.value.default_source_codes == ("source-a",)

    cleared = await service.clear_defaults(identity(), repository.value.generation)
    assert cleared.view is SettingsView.SAVED
    assert repository.value.default_location is None
    assert repository.value.default_source_codes == ()


async def test_unsupported_notification_preference_is_not_enabled() -> None:
    repository, service = await build(supports_filters=False)
    opened = await service.open(identity())
    assert opened.preferences

    result = await service.update_notifications(
        identity(),
        opened.preferences.generation,
        1,
    )

    assert result.view is SettingsView.INPUT_ERROR
    assert repository.value
    assert not repository.value.filters.notify_low_stock


async def test_timezone_and_quota_screen_are_explicit() -> None:
    repository, service = await build()
    repository.active_subscriptions = 2
    opened = await service.open(identity())
    assert opened.preferences
    changed = await service.set_timezone(identity(), opened.preferences.generation, 2)
    assert changed.preferences
    limits_result = await service.show_section(
        identity(),
        changed.preferences.generation,
        5,
    )

    assert changed.preferences.timezone_name == "Asia/Yekaterinburg"
    assert limits_result.view is SettingsView.LIMITS
    assert limits_result.usage
    assert limits_result.usage.active_subscriptions == 2

    rendered = render_user_settings(limits_result)
    assert "При достижении лимита" in rendered.text
    assert rendered.reply_markup
    assert all(
        len(button.callback_data or "") <= 64
        for row in rendered.reply_markup.inline_keyboard
        for button in row
    )
