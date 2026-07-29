from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pharmacy_bot.application.onboarding import (
    OnboardingResult,
    OnboardingService,
    OnboardingView,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity


class NavigationTarget(StrEnum):
    MAIN_MENU = "main"
    ADD_SUBSCRIPTION = "add"
    SUBSCRIPTIONS = "subscriptions"
    CHECK_AVAILABILITY = "check"
    LOCATION = "location"
    SETTINGS = "settings"
    HELP = "help"
    PRIVACY = "privacy"
    CANCEL = "cancel"
    UNKNOWN = "unknown"


class NavigationView(StrEnum):
    ONBOARDING = "onboarding"
    MAIN_MENU = "main_menu"
    FEATURE_ENTRY = "feature_entry"
    HELP = "help"
    PRIVACY = "privacy"
    CANCELLED = "cancelled"
    UNKNOWN_INPUT = "unknown_input"


@dataclass(frozen=True, slots=True)
class NavigationResult:
    view: NavigationView
    target: NavigationTarget
    onboarding: OnboardingResult


class NavigationService:
    def __init__(self, onboarding_service: OnboardingService) -> None:
        self._onboarding_service = onboarding_service

    async def navigate(
        self,
        identity: TelegramIdentity,
        target: NavigationTarget,
    ) -> NavigationResult:
        onboarding = await self._onboarding_service.start(identity)

        if target is NavigationTarget.HELP:
            return self._result(NavigationView.HELP, target, onboarding)
        if target is NavigationTarget.PRIVACY:
            return self._result(NavigationView.PRIVACY, target, onboarding)
        if target is NavigationTarget.UNKNOWN:
            return self._result(NavigationView.UNKNOWN_INPUT, target, onboarding)

        if onboarding.view is not OnboardingView.MAIN_MENU:
            return self._result(NavigationView.ONBOARDING, target, onboarding)

        if target is NavigationTarget.MAIN_MENU:
            return self._result(NavigationView.MAIN_MENU, target, onboarding)
        if target is NavigationTarget.CANCEL:
            return self._result(NavigationView.CANCELLED, target, onboarding)

        return self._result(NavigationView.FEATURE_ENTRY, target, onboarding)

    @staticmethod
    def _result(
        view: NavigationView,
        target: NavigationTarget,
        onboarding: OnboardingResult,
    ) -> NavigationResult:
        return NavigationResult(view=view, target=target, onboarding=onboarding)
