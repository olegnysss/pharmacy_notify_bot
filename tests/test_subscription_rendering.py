from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from pharmacy_bot.application.onboarding import (
    DocumentBundle,
    OnboardingResult,
    OnboardingView,
)
from pharmacy_bot.application.subscriptions import SubscriptionResult, SubscriptionView
from pharmacy_bot.domain.onboarding import (
    OnboardingStatus,
    TelegramIdentity,
    UserSnapshot,
)
from pharmacy_bot.domain.subscription_setup import AvailabilityState
from pharmacy_bot.presentation.subscription_rendering import render_subscriptions
from tests.subscription_fakes import subscription


def onboarding() -> OnboardingResult:
    return OnboardingResult(
        OnboardingView.MAIN_MENU,
        UserSnapshot(1, TelegramIdentity(1, 1, "ru"), OnboardingStatus.COMPLETED),
        DocumentBundle(
            "terms-v1",
            "https://example.com/terms",
            "privacy-v1",
            "https://example.com/privacy",
        ),
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (AvailabilityState.SOURCE_ERROR, "не означает отсутствие"),
        (AvailabilityState.UNKNOWN, "достоверных данных пока нет"),
        (AvailabilityState.STALE, "данные устарели"),
    ],
)
def test_uncertain_states_never_look_like_confirmed_absence(
    state: AvailabilityState,
    expected: str,
) -> None:
    item = replace(
        subscription(1, state=state),
        last_successful_check_at=datetime.now(UTC) - timedelta(hours=2),
        freshness_expires_at=datetime.now(UTC) - timedelta(hours=1),
    )

    rendered = render_subscriptions(
        SubscriptionResult(
            SubscriptionView.DETAILS,
            onboarding(),
            subscription=item,
        )
    )

    assert expected in rendered.text
    assert "нет в наличии" not in rendered.text
