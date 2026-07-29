from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message

from pharmacy_bot.application.subscription_lifecycle import (
    LifecycleResult,
    SubscriptionLifecycleService,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import CompletionMode, LocationInputMode
from pharmacy_bot.presentation.callbacks import (
    LifecycleCallback,
    SubscriptionViewCallback,
)
from pharmacy_bot.presentation.lifecycle_rendering import render_lifecycle
from pharmacy_bot.presentation.onboarding_router import identity_from_telegram

router = Router(name=__name__)


@router.callback_query(
    SubscriptionViewCallback.filter(F.action.in_({"edit", "lifecycle", "delete"}))
)
async def enter_lifecycle(
    callback: CallbackQuery,
    callback_data: SubscriptionViewCallback,
    subscription_lifecycle_service: SubscriptionLifecycleService,
) -> None:
    async def operation(identity: TelegramIdentity) -> LifecycleResult:
        if callback_data.action == "edit":
            return await subscription_lifecycle_service.start_edit(
                identity,
                callback_data.subscription_id,
            )
        if callback_data.action == "lifecycle":
            return await subscription_lifecycle_service.toggle_pause(
                identity,
                callback_data.subscription_id,
            )
        return await subscription_lifecycle_service.request_delete(
            identity,
            callback_data.subscription_id,
        )

    await _edit(callback, operation)


@router.callback_query(LifecycleCallback.filter())
async def handle_lifecycle_callback(
    callback: CallbackQuery,
    callback_data: LifecycleCallback,
    subscription_lifecycle_service: SubscriptionLifecycleService,
) -> None:
    action = callback_data.action
    generation = callback_data.generation
    value = callback_data.value

    async def operation(identity: TelegramIdentity) -> LifecycleResult:
        if action == "edit":
            return await subscription_lifecycle_service.start_edit(
                identity,
                callback_data.subscription_id,
            )
        if action == "block":
            return await subscription_lifecycle_service.choose_block(
                identity,
                generation,
                value,
            )
        if action == "location_mode":
            modes = {
                1: LocationInputMode.CITY,
                2: LocationInputMode.ADDRESS,
                3: LocationInputMode.COORDINATES,
            }
            mode = modes.get(value)
            if mode:
                return await subscription_lifecycle_service.choose_location_mode(
                    identity,
                    generation,
                    mode,
                )
        if action == "select_location":
            return await subscription_lifecycle_service.select_location(
                identity,
                generation,
                value,
            )
        if action == "radius":
            return await subscription_lifecycle_service.set_radius(
                identity,
                generation,
                value,
            )
        if action == "source":
            return await subscription_lifecycle_service.toggle_source(
                identity,
                generation,
                value,
            )
        if action == "sources_done":
            return await subscription_lifecycle_service.finish_sources(
                identity,
                generation,
            )
        if action == "filter":
            return await subscription_lifecycle_service.toggle_filter(
                identity,
                generation,
                value,
            )
        if action == "filters_done":
            return await subscription_lifecycle_service.finish_filters(
                identity,
                generation,
            )
        if action == "completion":
            completion_modes = {
                1: CompletionMode.CONTINUE,
                2: CompletionMode.PAUSE_AFTER_SUCCESS,
                3: CompletionMode.COMPLETE_AFTER_SUCCESS,
                4: CompletionMode.UNTIL_DATE,
            }
            completion_mode = completion_modes.get(value)
            if completion_mode:
                return await subscription_lifecycle_service.choose_completion(
                    identity,
                    generation,
                    completion_mode,
                )
        if action == "back":
            return await subscription_lifecycle_service.back_to_blocks(
                identity,
                generation,
            )
        if action == "apply":
            return await subscription_lifecycle_service.apply(identity, generation)
        if action == "cancel":
            cancelled = await subscription_lifecycle_service.cancel_edit(identity)
            if cancelled:
                return cancelled
        if action == "delete_confirm":
            return await subscription_lifecycle_service.confirm_delete(
                identity,
                callback_data.subscription_id,
                generation,
            )
        return await subscription_lifecycle_service.back_to_blocks(identity, -1)

    await _edit(callback, operation)


@router.message(F.chat.type == ChatType.PRIVATE, F.location)
async def handle_lifecycle_location(
    message: Message,
    subscription_lifecycle_service: SubscriptionLifecycleService,
) -> None:
    if message.from_user is None or message.location is None:
        raise SkipHandler
    result = await subscription_lifecycle_service.submit_coordinates(
        identity_from_telegram(message.from_user, message.chat.id),
        message.location.latitude,
        message.location.longitude,
    )
    if result is None:
        raise SkipHandler
    rendered = render_lifecycle(result)
    await message.answer(rendered.text, reply_markup=rendered.reply_markup)


@router.message(
    F.chat.type == ChatType.PRIVATE,
    F.text,
    ~F.text.startswith("/"),
)
async def handle_lifecycle_text(
    message: Message,
    subscription_lifecycle_service: SubscriptionLifecycleService,
) -> None:
    if message.from_user is None or message.text is None:
        raise SkipHandler
    identity = identity_from_telegram(message.from_user, message.chat.id)
    if not await subscription_lifecycle_service.accepts_text(identity):
        raise SkipHandler
    result = await subscription_lifecycle_service.submit_text(identity, message.text)
    if result is None:
        raise SkipHandler
    rendered = render_lifecycle(result)
    await message.answer(rendered.text, reply_markup=rendered.reply_markup)


async def _edit(
    callback: CallbackQuery,
    operation: Callable[[TelegramIdentity], Awaitable[LifecycleResult]],
) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        await callback.answer(
            "Кнопка устарела. Откройте /subscriptions.",
            show_alert=True,
        )
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Управление доступно только в личном чате.",
            show_alert=True,
        )
        return
    result = await operation(identity_from_telegram(callback.from_user, callback.message.chat.id))
    rendered = render_lifecycle(result)
    await callback.message.edit_text(rendered.text, reply_markup=rendered.reply_markup)
    await callback.answer()
