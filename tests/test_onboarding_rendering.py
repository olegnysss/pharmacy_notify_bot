from __future__ import annotations

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
from pharmacy_bot.presentation.rendering import render_help, render_onboarding


def result(view: OnboardingView) -> OnboardingResult:
    return OnboardingResult(
        view=view,
        user=UserSnapshot(
            id=1,
            identity=TelegramIdentity(telegram_user_id=10, telegram_chat_id=10),
            status=OnboardingStatus.AWAITING_CONSENT,
        ),
        documents=DocumentBundle(
            terms_version="terms-v1",
            terms_url="https://example.com/terms",
            privacy_version="privacy-v1",
            privacy_url="https://example.com/privacy",
        ),
    )


def callback_values(view: OnboardingView) -> set[str]:
    markup = render_onboarding(result(view)).reply_markup
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def url_values(view: OnboardingView) -> set[str]:
    markup = render_onboarding(result(view)).reply_markup
    return {
        button.url for row in markup.inline_keyboard for button in row if button.url is not None
    }


def test_welcome_explains_key_limitations_before_requesting_data() -> None:
    rendered = render_onboarding(result(OnboardingView.WELCOME))

    assert "не гарантирует" in rendered.text
    assert "не даёт медицинских рекомендаций" in rendered.text
    assert callback_values(OnboardingView.WELCOME) == {
        "onboarding:continue",
        "onboarding:documents",
        "onboarding:help",
    }


def test_consent_screen_displays_versions_urls_and_explicit_decisions() -> None:
    rendered = render_onboarding(result(OnboardingView.CONSENT_REQUIRED))

    assert "terms-v1" in rendered.text
    assert "privacy-v1" in rendered.text
    assert url_values(OnboardingView.CONSENT_REQUIRED) == {
        "https://example.com/terms",
        "https://example.com/privacy",
    }
    assert {"onboarding:accept", "onboarding:decline"} <= callback_values(
        OnboardingView.CONSENT_REQUIRED
    )


def test_completed_screen_exposes_future_subscription_entrypoint_and_menu() -> None:
    assert callback_values(OnboardingView.COMPLETED) == {
        "subscription:start",
        "navigation:main",
    }


def test_returning_user_gets_main_menu_entrypoints() -> None:
    assert callback_values(OnboardingView.MAIN_MENU) == {
        "subscription:start",
        "navigation:subscriptions",
        "navigation:check",
        "navigation:location",
        "navigation:settings",
        "navigation:help",
        "navigation:privacy",
    }


def test_declined_screen_keeps_documents_help_and_reconsideration_available() -> None:
    rendered = render_onboarding(result(OnboardingView.DECLINED))

    assert "Мониторинг и создание подписок недоступны" in rendered.text
    assert "onboarding:continue" in callback_values(OnboardingView.DECLINED)
    assert url_values(OnboardingView.DECLINED) == {
        "https://example.com/terms",
        "https://example.com/privacy",
    }


def test_help_is_available_without_consent_and_repeats_disclaimer() -> None:
    rendered = render_help(result(OnboardingView.WELCOME))

    assert "не гарантирует" in rendered.text
    assert "не даёт медицинских рекомендаций" in rendered.text
    assert {button.url for button in rendered.reply_markup.inline_keyboard[0]} == {
        "https://example.com/terms",
        "https://example.com/privacy",
    }
