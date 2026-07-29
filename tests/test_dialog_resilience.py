from __future__ import annotations

from datetime import UTC, datetime

from pharmacy_bot.application.dialog_recovery import DialogRecoveryService
from pharmacy_bot.application.localization import MessageKey, Translator
from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.domain.dialog import DialogRecovery, DialogScenario, RecoveryState
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.presentation.rendering import render_onboarding
from tests.fakes import InMemoryOnboardingRepository


class RecoveryRepository:
    def __init__(self, recovery: DialogRecovery) -> None:
        self.recovery = recovery
        self.calls: list[tuple[int, datetime, int]] = []

    async def inspect_and_cleanup(
        self,
        user_id: int,
        *,
        now: datetime,
        schema_version: int,
    ) -> DialogRecovery:
        self.calls.append((user_id, now, schema_version))
        return self.recovery


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 29, 19, 0, tzinfo=UTC)


async def test_active_dialog_is_rendered_as_safe_resume_action() -> None:
    onboarding = OnboardingService(
        InMemoryOnboardingRepository(),
        DocumentBundle(
            "terms-v1",
            "https://example.com/terms",
            "privacy-v1",
            "https://example.com/privacy",
        ),
    )
    identity = TelegramIdentity(1001, 1001, "ru")
    await onboarding.accept(identity)
    repository = RecoveryRepository(
        DialogRecovery(
            RecoveryState.ACTIVE,
            DialogScenario.SUBSCRIPTION_EDIT,
            subscription_id=42,
        )
    )
    service = DialogRecoveryService(onboarding, repository, clock=FixedClock())

    recovery = await service.inspect(identity)
    rendered = render_onboarding(await onboarding.start(identity), recovery)

    assert "Найден незавершённый сценарий" in rendered.text
    callback = rendered.reply_markup.inline_keyboard[0][0].callback_data
    assert callback and "42" in callback
    assert repository.calls[0][2] == 1


def test_localized_safe_errors_have_fallback_and_correlation_id() -> None:
    translator = Translator()

    english = translator.text(
        MessageKey.INTERNAL_ERROR,
        "en-US",
        correlation_id="support-123",
    )
    fallback = translator.text(MessageKey.DUPLICATE_UPDATE, "unknown")

    assert "support-123" in english
    assert "internal error" in english
    assert fallback == "Это действие уже обработано."


def test_reset_dialog_explains_that_no_business_result_was_created() -> None:
    assert "не создана" in Translator().text(MessageKey.RECOVERY_RESET, "ru")
