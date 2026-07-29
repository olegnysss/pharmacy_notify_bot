from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from pharmacy_bot.application.onboarding import (
    DocumentBundle,
    OnboardingResult,
    OnboardingView,
)
from pharmacy_bot.application.subscription_setup import SetupResult, SetupView
from pharmacy_bot.domain.onboarding import (
    OnboardingStatus,
    TelegramIdentity,
    UserSnapshot,
)
from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    MonitoringFilters,
    ProductSnapshot,
    SetupStatus,
    SourceOption,
    SubscriptionSetupDraft,
)
from pharmacy_bot.presentation.subscription_setup_rendering import (
    render_subscription_setup,
)


def onboarding() -> OnboardingResult:
    return OnboardingResult(
        OnboardingView.MAIN_MENU,
        UserSnapshot(
            id=1,
            identity=TelegramIdentity(1, 1, "ru"),
            status=OnboardingStatus.COMPLETED,
        ),
        DocumentBundle(
            "terms-v1",
            "https://example.com/terms",
            "privacy-v1",
            "https://example.com/privacy",
        ),
    )


def review_draft() -> SubscriptionSetupDraft:
    location = LocationCandidate(
        key="city:moscow",
        kind=LocationInputMode.CITY,
        display_name="Москва",
        city="Москва",
        confidence=LocationConfidence.EXACT,
    )
    return SubscriptionSetupDraft(
        id=10,
        user_id=1,
        generation=8,
        status=SetupStatus.REVIEW,
        product=ProductSnapshot(
            "product-1",
            "v1",
            "Товар",
            "таблетки",
            "10 мг",
            "№20",
            "Производитель",
            "source.example",
        ),
        location_mode=LocationInputMode.CITY,
        location_candidates=(location,),
        location=location,
        radius_meters=5000,
        available_sources=(SourceOption("source-a", "Аптека A", True, ordinal=0),),
        selected_source_codes=("source-a",),
        filters=MonitoringFilters(notify_low_stock=True),
        completion_mode=CompletionMode.CONTINUE,
        ends_at=None,
        idempotency_key="safe-server-key",
        expires_at=datetime(2026, 7, 30, tzinfo=UTC),
    )


def test_review_shows_complete_rule_and_callbacks_contain_only_server_coordinates() -> None:
    rendered = render_subscription_setup(
        SetupResult(SetupView.REVIEW, onboarding(), review_draft())
    )

    assert "Товар" in rendered.text
    assert "Москва" in rendered.text
    assert "5 км" in rendered.text
    assert "Аптека A" in rendered.text
    assert "продолжать мониторинг" in rendered.text
    callbacks = [
        button.callback_data
        for row in rendered.reply_markup.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert callbacks
    assert all("Москва" not in callback for callback in callbacks)
    assert all("Товар" not in callback for callback in callbacks)
    assert all("safe-server-key" not in callback for callback in callbacks)


def test_created_screen_calls_pending_state_neither_absence_nor_appearance() -> None:
    draft = review_draft()
    rendered = render_subscription_setup(
        SetupResult(
            SetupView.CREATED,
            onboarding(),
            draft=replace(draft, status=SetupStatus.CREATED, subscription_id=42),
        )
    )

    assert "ожидает первой проверки" in rendered.text
    assert "не означает отсутствие" in rendered.text
    assert "товар появился" not in rendered.text.casefold()
