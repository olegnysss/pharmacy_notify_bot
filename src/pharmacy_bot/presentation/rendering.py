from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pharmacy_bot.application.onboarding import OnboardingResult, OnboardingView
from pharmacy_bot.presentation.callbacks import (
    NavigationCallback,
    OnboardingCallback,
    SubscriptionCallback,
)


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    reply_markup: InlineKeyboardMarkup


def render_onboarding(result: OnboardingResult) -> RenderedMessage:
    renderers = {
        OnboardingView.WELCOME: _render_welcome,
        OnboardingView.CONSENT_REQUIRED: _render_consent,
        OnboardingView.COMPLETED: _render_completed,
        OnboardingView.DECLINED: _render_declined,
        OnboardingView.MAIN_MENU: _render_main_menu,
    }
    return renderers[result.view](result)


def render_help(result: OnboardingResult) -> RenderedMessage:
    return RenderedMessage(
        text=(
            "Бот отслеживает данные разрешённых аптечных источников и сообщает об изменениях.\n\n"
            "Он не продаёт товары, не гарантирует фактический остаток и не даёт медицинских "
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
                        text="Продолжить",
                        callback_data=OnboardingCallback(action="continue").pack(),
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


def _render_main_menu(result: OnboardingResult) -> RenderedMessage:
    return RenderedMessage(
        text="Главное меню",
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
                        text="Мои подписки",
                        callback_data=NavigationCallback(action="subscriptions").pack(),
                    ),
                    InlineKeyboardButton(
                        text="Настройки",
                        callback_data=NavigationCallback(action="settings").pack(),
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
