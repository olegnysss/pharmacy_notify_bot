from __future__ import annotations

import pytest

from pharmacy_bot.application.navigation import (
    NavigationService,
    NavigationTarget,
    NavigationView,
)
from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.domain.onboarding import TelegramIdentity
from tests.fakes import InMemoryOnboardingRepository


@pytest.fixture
def identity() -> TelegramIdentity:
    return TelegramIdentity(telegram_user_id=1001, telegram_chat_id=1001)


@pytest.fixture
def onboarding_service() -> OnboardingService:
    return OnboardingService(
        InMemoryOnboardingRepository(),
        DocumentBundle(
            terms_version="terms-v1",
            terms_url="https://example.com/terms",
            privacy_version="privacy-v1",
            privacy_url="https://example.com/privacy",
        ),
    )


@pytest.fixture
def navigation_service(onboarding_service: OnboardingService) -> NavigationService:
    return NavigationService(onboarding_service)


@pytest.mark.parametrize(
    ("target", "view"),
    [
        (NavigationTarget.HELP, NavigationView.HELP),
        (NavigationTarget.PRIVACY, NavigationView.PRIVACY),
        (NavigationTarget.UNKNOWN, NavigationView.UNKNOWN_INPUT),
    ],
)
async def test_public_navigation_is_available_before_consent(
    navigation_service: NavigationService,
    identity: TelegramIdentity,
    target: NavigationTarget,
    view: NavigationView,
) -> None:
    result = await navigation_service.navigate(identity, target)

    assert result.view is view


@pytest.mark.parametrize(
    "target",
    [
        NavigationTarget.MAIN_MENU,
        NavigationTarget.ADD_SUBSCRIPTION,
        NavigationTarget.SUBSCRIPTIONS,
        NavigationTarget.CHECK_AVAILABILITY,
        NavigationTarget.LOCATION,
        NavigationTarget.SETTINGS,
        NavigationTarget.CANCEL,
    ],
)
async def test_protected_navigation_cannot_bypass_consent(
    navigation_service: NavigationService,
    identity: TelegramIdentity,
    target: NavigationTarget,
) -> None:
    result = await navigation_service.navigate(identity, target)

    assert result.view is NavigationView.ONBOARDING


async def test_command_and_button_targets_share_the_same_application_route(
    navigation_service: NavigationService,
    onboarding_service: OnboardingService,
    identity: TelegramIdentity,
) -> None:
    await onboarding_service.accept(identity)

    first = await navigation_service.navigate(identity, NavigationTarget.SETTINGS)
    second = await navigation_service.navigate(identity, NavigationTarget.SETTINGS)

    assert first.view is NavigationView.FEATURE_ENTRY
    assert second == first


async def test_repeated_cancel_is_safe_and_returns_cancelled_view(
    navigation_service: NavigationService,
    onboarding_service: OnboardingService,
    identity: TelegramIdentity,
) -> None:
    await onboarding_service.accept(identity)

    first = await navigation_service.navigate(identity, NavigationTarget.CANCEL)
    second = await navigation_service.navigate(identity, NavigationTarget.CANCEL)

    assert first.view is NavigationView.CANCELLED
    assert second.view is NavigationView.CANCELLED
