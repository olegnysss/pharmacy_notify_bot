from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pharmacy_bot.application.localization import MessageKey, Translator
from pharmacy_bot.application.onboarding import OnboardingResult, OnboardingView
from pharmacy_bot.domain.dialog import DialogRecovery, DialogScenario, RecoveryState
from pharmacy_bot.presentation.callbacks import (
    LifecycleCallback,
    NavigationCallback,
    OnboardingCallback,
    SubscriptionCallback,
)


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    reply_markup: InlineKeyboardMarkup


def main_menu_markup(recovery: DialogRecovery | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if recovery and recovery.state is RecoveryState.ACTIVE and recovery.scenario:
        rows.append([_recovery_button(recovery)])
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Добавить товар",
                    callback_data=SubscriptionCallback(action="start").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Мои подписки",
                    callback_data=NavigationCallback(action="subscriptions").pack(),
                ),
                InlineKeyboardButton(
                    text="Проверить наличие",
                    callback_data=NavigationCallback(action="check").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Локация",
                    callback_data=NavigationCallback(action="location").pack(),
                ),
                InlineKeyboardButton(
                    text="Настройки",
                    callback_data=NavigationCallback(action="settings").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Помощь",
                    callback_data=NavigationCallback(action="help").pack(),
                ),
                InlineKeyboardButton(
                    text="Конфиденциальность",
                    callback_data=NavigationCallback(action="privacy").pack(),
                ),
            ],
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=rows,
    )


def render_onboarding(
    result: OnboardingResult,
    recovery: DialogRecovery | None = None,
) -> RenderedMessage:
    if result.view is OnboardingView.MAIN_MENU:
        return _render_main_menu(result, recovery)
    renderers = {
        OnboardingView.WELCOME: _render_welcome,
        OnboardingView.CONSENT_REQUIRED: _render_consent,
        OnboardingView.COMPLETED: _render_completed,
        OnboardingView.DECLINED: _render_declined,
    }
    return renderers[result.view](result)


def render_help(result: OnboardingResult) -> RenderedMessage:
    has_access = result.view is OnboardingView.MAIN_MENU
    return RenderedMessage(
        text=(
            "Помощь\n\n"
            "• «Добавить товар» запускает настройку новой подписки.\n"
            "• «Мои подписки» позволяет просматривать, приостанавливать и прекращать "
            "мониторинг.\n"
            "• /cancel безопасно отменяет незавершённый сценарий.\n\n"
            "Бот отслеживает данные разрешённых аптечных источников. Данные могут обновляться "
            "с задержкой: проверяйте источник и время последней проверки.\n\n"
            "Бот не продаёт товары, не гарантирует фактический остаток и не даёт медицинских "
            "рекомендаций. Перед поездкой или покупкой перепроверьте данные у аптеки."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Условия использования",
                        url=result.documents.terms_url,
                    ),
                    InlineKeyboardButton(
                        text="Конфиденциальность",
                        url=result.documents.privacy_url,
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Главное меню" if has_access else "Продолжить",
                        callback_data=(
                            NavigationCallback(action="main").pack()
                            if has_access
                            else OnboardingCallback(action="continue").pack()
                        ),
                    )
                ],
            ]
        ),
    )


def _render_welcome(result: OnboardingResult) -> RenderedMessage:
    return RenderedMessage(
        text=(
            "Этот бот поможет следить за появлением выбранного аптечного товара.\n\n"
            "Он проверяет данные разрешённых источников, но не продаёт товары, не гарантирует "
            "фактическое наличие и не даёт медицинских рекомендаций.\n\n"
            "Перед началом ознакомьтесь с условиями и политикой конфиденциальности."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Продолжить",
                        callback_data=OnboardingCallback(action="continue").pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Документы",
                        callback_data=OnboardingCallback(action="documents").pack(),
                    ),
                    InlineKeyboardButton(
                        text="Помощь",
                        callback_data=OnboardingCallback(action="help").pack(),
                    ),
                ],
            ]
        ),
    )


def _render_consent(result: OnboardingResult) -> RenderedMessage:
    documents = result.documents
    return RenderedMessage(
        text=(
            "Для запуска мониторинга нужно принять обязательные документы:\n\n"
            f"• Условия использования — версия {documents.terms_version}\n"
            f"• Политика конфиденциальности — версия {documents.privacy_version}\n\n"
            "Нажатие «Принимаю» относится только к указанным версиям."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Условия",
                        url=documents.terms_url,
                    ),
                    InlineKeyboardButton(
                        text="Конфиденциальность",
                        url=documents.privacy_url,
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Принимаю",
                        callback_data=OnboardingCallback(action="accept").pack(),
                    ),
                    InlineKeyboardButton(
                        text="Не принимаю",
                        callback_data=OnboardingCallback(action="decline").pack(),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Помощь",
                        callback_data=OnboardingCallback(action="help").pack(),
                    )
                ],
            ]
        ),
    )


def _render_completed(result: OnboardingResult) -> RenderedMessage:
    return RenderedMessage(
        text=(
            "Onboarding завершён. Теперь можно создать первую подписку на товар "
            "или перейти в главное меню.\n\n"
            "Для поиска могут понадобиться город или географическая область, но точная "
            "геопозиция не будет запрошена без отдельного объяснения и подтверждения."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Добавить товар",
                        callback_data=SubscriptionCallback(action="start").pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Главное меню",
                        callback_data=NavigationCallback(action="main").pack(),
                    )
                ],
            ]
        ),
    )


def _render_declined(result: OnboardingResult) -> RenderedMessage:
    return RenderedMessage(
        text=(
            "Вы не приняли обязательные документы. Мониторинг и создание подписок недоступны.\n\n"
            "Можно повторно открыть документы, получить справку или вернуться к решению позже."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Пересмотреть решение",
                        callback_data=OnboardingCallback(action="continue").pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Условия",
                        url=result.documents.terms_url,
                    ),
                    InlineKeyboardButton(
                        text="Конфиденциальность",
                        url=result.documents.privacy_url,
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Помощь",
                        callback_data=OnboardingCallback(action="help").pack(),
                    )
                ],
            ]
        ),
    )


def _render_main_menu(
    result: OnboardingResult,
    recovery: DialogRecovery | None = None,
) -> RenderedMessage:
    recovery_text = ""
    if recovery and recovery.state in {RecoveryState.ACTIVE, RecoveryState.RESET}:
        key = (
            MessageKey.RECOVERY_ACTIVE
            if recovery.state is RecoveryState.ACTIVE
            else MessageKey.RECOVERY_RESET
        )
        recovery_text = "\n\n" + Translator().text(
            key,
            result.user.identity.language_code,
        )
    return RenderedMessage(
        text=(
            "Главное меню\n\n"
            "Выберите действие. Бот сообщает данные аптечных источников, "
            f"но не гарантирует фактическое наличие товара.{recovery_text}"
        ),
        reply_markup=main_menu_markup(recovery),
    )


def _recovery_button(recovery: DialogRecovery) -> InlineKeyboardButton:
    if recovery.scenario is DialogScenario.PRODUCT_SELECTION:
        return InlineKeyboardButton(
            text="Продолжить выбор товара",
            callback_data=SubscriptionCallback(action="start").pack(),
        )
    if recovery.scenario is DialogScenario.SUBSCRIPTION_SETUP:
        return InlineKeyboardButton(
            text="Продолжить настройку подписки",
            callback_data=SubscriptionCallback(action="configure").pack(),
        )
    if recovery.scenario is DialogScenario.SUBSCRIPTION_EDIT and recovery.subscription_id:
        return InlineKeyboardButton(
            text="Продолжить изменение подписки",
            callback_data=LifecycleCallback(
                action="edit",
                subscription_id=recovery.subscription_id,
                generation=0,
                value=0,
            ).pack(),
        )
    return InlineKeyboardButton(
        text="Продолжить настройку профиля",
        callback_data=NavigationCallback(action="settings").pack(),
    )
