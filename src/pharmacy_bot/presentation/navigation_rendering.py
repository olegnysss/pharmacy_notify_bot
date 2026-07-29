from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pharmacy_bot.application.navigation import (
    NavigationResult,
    NavigationTarget,
    NavigationView,
)
from pharmacy_bot.application.onboarding import OnboardingView
from pharmacy_bot.presentation.callbacks import (
    NavigationCallback,
    OnboardingCallback,
)
from pharmacy_bot.presentation.rendering import (
    RenderedMessage,
    main_menu_markup,
    render_help,
    render_onboarding,
)


def render_navigation(result: NavigationResult) -> RenderedMessage:
    if result.view is NavigationView.ONBOARDING:
        return render_onboarding(result.onboarding)
    if result.view is NavigationView.MAIN_MENU:
        return _render_main_menu()
    if result.view is NavigationView.HELP:
        return render_help(result.onboarding)
    if result.view is NavigationView.PRIVACY:
        return _render_privacy(result)
    if result.view is NavigationView.CANCELLED:
        return _render_cancelled()
    if result.view is NavigationView.UNKNOWN_INPUT:
        return _render_unknown(result)
    return _render_feature_entry(result.target)


def _render_main_menu() -> RenderedMessage:
    return RenderedMessage(
        text=(
            "Главное меню\n\n"
            "Выберите действие. Бот сообщает данные аптечных источников, "
            "но не гарантирует фактическое наличие товара."
        ),
        reply_markup=main_menu_markup(),
    )


def _render_feature_entry(target: NavigationTarget) -> RenderedMessage:
    descriptions = {
        NavigationTarget.ADD_SUBSCRIPTION: (
            "Создание подписки начнётся здесь. Выбор и подтверждение товара "
            "будут реализованы в следующей story."
        ),
        NavigationTarget.SUBSCRIPTIONS: (
            "Здесь будет список активных и приостановленных подписок."
        ),
        NavigationTarget.CHECK_AVAILABILITY: (
            "Ручная проверка будет доступна после реализации подписок и безопасных лимитов."
        ),
        NavigationTarget.LOCATION: (
            "Настройка города и области мониторинга будет реализована отдельной story."
        ),
        NavigationTarget.SETTINGS: "Пользовательские настройки будут реализованы отдельной story.",
    }
    text = descriptions.get(target, "Этот раздел пока недоступен.")
    return RenderedMessage(
        text=f"{text}\n\nНикакие данные или подписки пока не созданы.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data=NavigationCallback(action="main").pack(),
                    ),
                    InlineKeyboardButton(
                        text="Отменить",
                        callback_data=NavigationCallback(action="cancel").pack(),
                    ),
                ]
            ]
        ),
    )


def _render_privacy(result: NavigationResult) -> RenderedMessage:
    documents = result.onboarding.documents
    has_access = result.onboarding.view is OnboardingView.MAIN_MENU
    return RenderedMessage(
        text=(
            "Конфиденциальность и данные\n\n"
            "Бот хранит только данные, необходимые для работы сервиса. "
            "Интерес к аптечному товару может быть чувствительной информацией.\n\n"
            f"Политика конфиденциальности — версия {documents.privacy_version}."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Открыть политику",
                        url=documents.privacy_url,
                    ),
                    InlineKeyboardButton(
                        text="Условия",
                        url=documents.terms_url,
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


def _render_cancelled() -> RenderedMessage:
    return RenderedMessage(
        text=(
            "Текущий сценарий отменён. Незавершённые действия не создали "
            "подписку или другой бизнес-результат."
        ),
        reply_markup=main_menu_markup(),
    )


def _render_unknown(result: NavigationResult) -> RenderedMessage:
    has_access = result.onboarding.view is OnboardingView.MAIN_MENU
    return RenderedMessage(
        text=(
            "Не удалось распознать команду или сообщение. "
            "Используйте доступные кнопки либо команду /help."
        ),
        reply_markup=(
            main_menu_markup()
            if has_access
            else InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Помощь",
                            callback_data=NavigationCallback(action="help").pack(),
                        ),
                        InlineKeyboardButton(
                            text="Продолжить",
                            callback_data=OnboardingCallback(action="continue").pack(),
                        ),
                    ]
                ]
            )
        ),
    )
