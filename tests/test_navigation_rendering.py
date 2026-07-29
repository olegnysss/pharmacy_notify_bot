from __future__ import annotations

from pharmacy_bot.application.navigation import (
    NavigationResult,
    NavigationTarget,
    NavigationView,
)
from pharmacy_bot.application.onboarding import (
    DocumentBundle,
    OnboardingResult,
    OnboardingView,
)
from pharmacy_bot.domain.onboarding import (
    OnboardingStatus,
    TelegramIdentity,
    UserSnapshot,
)
from pharmacy_bot.presentation.navigation_rendering import render_navigation
from pharmacy_bot.presentation.rendering import RenderedMessage


def result(
    view: NavigationView,
    target: NavigationTarget,
    *,
    has_access: bool = True,
) -> NavigationResult:
    return NavigationResult(
        view=view,
        target=target,
        onboarding=OnboardingResult(
            view=OnboardingView.MAIN_MENU if has_access else OnboardingView.CONSENT_REQUIRED,
            user=UserSnapshot(
                id=1,
                identity=TelegramIdentity(telegram_user_id=10, telegram_chat_id=10),
                status=(
                    OnboardingStatus.COMPLETED if has_access else OnboardingStatus.AWAITING_CONSENT
                ),
            ),
            documents=DocumentBundle(
                terms_version="terms-v1",
                terms_url="https://example.com/terms",
                privacy_version="privacy-v1",
                privacy_url="https://example.com/privacy",
            ),
        ),
    )


def callback_values(rendered: RenderedMessage) -> set[str]:
    markup = rendered.reply_markup
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def test_main_menu_exposes_all_required_entrypoints_without_sensitive_ids() -> None:
    rendered = render_navigation(result(NavigationView.MAIN_MENU, NavigationTarget.MAIN_MENU))

    assert callback_values(rendered) == {
        "subscription:start",
        "navigation:subscriptions",
        "navigation:check",
        "navigation:location",
        "navigation:settings",
        "navigation:help",
        "navigation:privacy",
    }
    assert all(value.count(":") == 1 for value in callback_values(rendered))


def test_future_feature_entry_has_back_and_cancel_without_creating_data() -> None:
    rendered = render_navigation(
        result(NavigationView.FEATURE_ENTRY, NavigationTarget.ADD_SUBSCRIPTION)
    )

    assert "Никакие данные или подписки пока не созданы" in rendered.text
    assert callback_values(rendered) == {
        "navigation:main",
        "navigation:cancel",
    }


def test_help_explains_freshness_management_and_medical_limitations() -> None:
    rendered = render_navigation(result(NavigationView.HELP, NavigationTarget.HELP))

    assert "время последней проверки" in rendered.text
    assert "приостанавливать и прекращать" in rendered.text
    assert "не даёт медицинских рекомендаций" in rendered.text


def test_privacy_is_available_before_consent_and_returns_to_onboarding() -> None:
    rendered = render_navigation(
        result(
            NavigationView.PRIVACY,
            NavigationTarget.PRIVACY,
            has_access=False,
        )
    )

    assert "чувствительной информацией" in rendered.text
    assert "onboarding:continue" in callback_values(rendered)


def test_unknown_input_returns_a_useful_next_action() -> None:
    rendered = render_navigation(
        result(
            NavigationView.UNKNOWN_INPUT,
            NavigationTarget.UNKNOWN,
            has_access=False,
        )
    )

    assert "/help" in rendered.text
    assert callback_values(rendered) == {
        "navigation:help",
        "onboarding:continue",
    }
