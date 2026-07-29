from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, User

from pharmacy_bot.application.dialog_recovery import DialogRecoveryService
from pharmacy_bot.application.onboarding import OnboardingResult, OnboardingService
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.presentation.callbacks import OnboardingCallback
from pharmacy_bot.presentation.rendering import render_help, render_onboarding

router = Router(name=__name__)


def identity_from_telegram(user: User, chat_id: int) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=user.id,
        telegram_chat_id=chat_id,
        language_code=user.language_code,
    )


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start_onboarding(
    message: Message,
    onboarding_service: OnboardingService,
    dialog_recovery_service: DialogRecoveryService | None = None,
) -> None:
    if message.from_user is None:
        return
    result = await onboarding_service.start(
        identity_from_telegram(message.from_user, message.chat.id)
    )
    recovery = (
        await dialog_recovery_service.inspect(
            identity_from_telegram(message.from_user, message.chat.id)
        )
        if dialog_recovery_service
        else None
    )
    rendered = render_onboarding(result, recovery)
    await message.answer(rendered.text, reply_markup=rendered.reply_markup)


@router.callback_query(OnboardingCallback.filter(F.action == "continue"))
async def continue_onboarding(
    callback: CallbackQuery,
    onboarding_service: OnboardingService,
) -> None:
    await _apply_callback(callback, onboarding_service.continue_onboarding)


@router.callback_query(OnboardingCallback.filter(F.action == "documents"))
async def show_documents(
    callback: CallbackQuery,
    onboarding_service: OnboardingService,
) -> None:
    await _apply_callback(callback, onboarding_service.continue_onboarding)


@router.callback_query(OnboardingCallback.filter(F.action == "accept"))
async def accept_documents(
    callback: CallbackQuery,
    onboarding_service: OnboardingService,
) -> None:
    await _apply_callback(callback, onboarding_service.accept)


@router.callback_query(OnboardingCallback.filter(F.action == "decline"))
async def decline_documents(
    callback: CallbackQuery,
    onboarding_service: OnboardingService,
) -> None:
    await _apply_callback(callback, onboarding_service.decline)


@router.callback_query(OnboardingCallback.filter(F.action == "help"))
async def show_help(
    callback: CallbackQuery,
    onboarding_service: OnboardingService,
) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        await callback.answer("Не удалось открыть справку. Отправьте /start.", show_alert=True)
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Справка и управление доступны только в личном чате с ботом.",
            show_alert=True,
        )
        return

    identity = identity_from_telegram(callback.from_user, callback.message.chat.id)
    result = await onboarding_service.start(identity)
    rendered = render_help(result)
    await callback.message.edit_text(rendered.text, reply_markup=rendered.reply_markup)
    await callback.answer()


async def _apply_callback(
    callback: CallbackQuery,
    operation: Callable[[TelegramIdentity], Awaitable[OnboardingResult]],
) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        await callback.answer("Кнопка устарела. Отправьте /start.", show_alert=True)
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Управление доступно только в личном чате с ботом.",
            show_alert=True,
        )
        return

    identity = identity_from_telegram(callback.from_user, callback.message.chat.id)
    result = await operation(identity)
    rendered = render_onboarding(result)
    await callback.message.edit_text(rendered.text, reply_markup=rendered.reply_markup)
    await callback.answer()
