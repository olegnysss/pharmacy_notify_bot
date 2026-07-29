from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message

from pharmacy_bot.application.subscription_setup import (
    SetupResult,
    SubscriptionSetupService,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import CompletionMode, LocationInputMode
from pharmacy_bot.presentation.callbacks import SetupCallback, SubscriptionCallback
from pharmacy_bot.presentation.onboarding_router import identity_from_telegram
from pharmacy_bot.presentation.subscription_setup_rendering import (
    render_subscription_setup,
)

router = Router(name=__name__)


@router.callback_query(SubscriptionCallback.filter(F.action == "configure"))
async def start_setup(
    callback: CallbackQuery,
    subscription_setup_service: SubscriptionSetupService,
) -> None:
    await _edit(callback, subscription_setup_service.start)


@router.callback_query(SetupCallback.filter())
async def handle_setup_callback(
    callback: CallbackQuery,
    callback_data: SetupCallback,
    subscription_setup_service: SubscriptionSetupService,
) -> None:
    action = callback_data.action
    generation = callback_data.generation
    value = callback_data.value

    async def operation(identity: TelegramIdentity) -> SetupResult:
        if action == "location_mode":
            modes = {
                1: LocationInputMode.CITY,
                2: LocationInputMode.ADDRESS,
                3: LocationInputMode.COORDINATES,
            }
            mode = modes.get(value)
            if mode is not None:
                return await subscription_setup_service.choose_location_mode(
                    identity, mode, generation
                )
        if action == "select_location":
            return await subscription_setup_service.select_location(identity, generation, value)
        if action == "radius":
            return await subscription_setup_service.set_radius(identity, generation, value)
        if action == "source":
            return await subscription_setup_service.toggle_source(identity, generation, value)
        if action == "sources_done":
            return await subscription_setup_service.confirm_sources(identity, generation)
        if action == "filter":
            return await subscription_setup_service.toggle_filter(identity, generation, value)
        if action == "filters_done":
            return await subscription_setup_service.confirm_filters(identity, generation)
        if action == "completion":
            completion_modes = {
                1: CompletionMode.CONTINUE,
                2: CompletionMode.PAUSE_AFTER_SUCCESS,
                3: CompletionMode.COMPLETE_AFTER_SUCCESS,
                4: CompletionMode.UNTIL_DATE,
            }
            completion_mode = completion_modes.get(value)
            if completion_mode is not None:
                return await subscription_setup_service.choose_completion(
                    identity, generation, completion_mode
                )
        if action == "edit":
            return await subscription_setup_service.edit(identity, generation, value)
        if action == "edit_location":
            return await subscription_setup_service.edit(identity, generation, 1)
        if action == "edit_sources":
            return await subscription_setup_service.edit(identity, generation, 2)
        if action == "edit_filters":
            return await subscription_setup_service.edit(identity, generation, 3)
        if action == "edit_completion":
            return await subscription_setup_service.edit(identity, generation, 4)
        if action == "confirm":
            return await subscription_setup_service.confirm(identity, generation)
        if action == "cancel":
            return await subscription_setup_service.cancel(identity, generation)
        return await subscription_setup_service.edit(identity, -1, 1)

    await _edit(callback, operation)


@router.message(F.chat.type == ChatType.PRIVATE, F.location)
async def handle_setup_location(
    message: Message,
    subscription_setup_service: SubscriptionSetupService,
) -> None:
    if message.from_user is None or message.location is None:
        raise SkipHandler
    identity = identity_from_telegram(message.from_user, message.chat.id)
    result = await subscription_setup_service.submit_coordinates(
        identity,
        message.location.latitude,
        message.location.longitude,
    )
    if result is None:
        raise SkipHandler
    rendered = render_subscription_setup(result)
    await message.answer(rendered.text, reply_markup=rendered.reply_markup)


@router.message(
    F.chat.type == ChatType.PRIVATE,
    F.text,
    ~F.text.startswith("/"),
)
async def handle_setup_text(
    message: Message,
    subscription_setup_service: SubscriptionSetupService,
) -> None:
    if message.from_user is None or message.text is None:
        raise SkipHandler
    identity = identity_from_telegram(message.from_user, message.chat.id)
    if not await subscription_setup_service.accepts_text(identity):
        raise SkipHandler
    progress = await message.answer("Проверяю параметры…")
    result = await subscription_setup_service.submit_text(identity, message.text)
    if result is None:
        await progress.edit_text("Сценарий уже изменился. Откройте актуальный мастер настройки.")
        return
    rendered = render_subscription_setup(result)
    await progress.edit_text(rendered.text, reply_markup=rendered.reply_markup)


async def _edit(
    callback: CallbackQuery,
    operation: Callable[[TelegramIdentity], Awaitable[SetupResult]],
) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        await callback.answer(
            "Кнопка устарела. Откройте личный чат и отправьте /add.",
            show_alert=True,
        )
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Настройка доступна только в личном чате с ботом.",
            show_alert=True,
        )
        return
    identity = identity_from_telegram(callback.from_user, callback.message.chat.id)
    result = await operation(identity)
    rendered = render_subscription_setup(result)
    await callback.message.edit_text(rendered.text, reply_markup=rendered.reply_markup)
    await callback.answer()
