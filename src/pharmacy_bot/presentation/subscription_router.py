from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from pharmacy_bot.application.subscriptions import (
    SubscriptionFilter,
    SubscriptionQueryService,
    SubscriptionResult,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.presentation.callbacks import (
    NavigationCallback,
    SubscriptionViewCallback,
)
from pharmacy_bot.presentation.onboarding_router import identity_from_telegram
from pharmacy_bot.presentation.subscription_rendering import render_subscriptions

router = Router(name=__name__)


@router.message(Command("subscriptions"), F.chat.type == ChatType.PRIVATE)
async def list_by_command(
    message: Message,
    subscription_query_service: SubscriptionQueryService,
) -> None:
    await _answer(message, subscription_query_service.list)


@router.callback_query(NavigationCallback.filter(F.action == "subscriptions"))
async def list_by_navigation(
    callback: CallbackQuery,
    subscription_query_service: SubscriptionQueryService,
) -> None:
    await _edit(callback, subscription_query_service.list)


@router.callback_query(NavigationCallback.filter(F.action == "check"))
async def choose_subscription_to_check(
    callback: CallbackQuery,
    subscription_query_service: SubscriptionQueryService,
) -> None:
    await _edit(callback, subscription_query_service.list)


@router.callback_query(SubscriptionViewCallback.filter())
async def handle_subscription_callback(
    callback: CallbackQuery,
    callback_data: SubscriptionViewCallback,
    subscription_query_service: SubscriptionQueryService,
) -> None:
    filters = {
        0: SubscriptionFilter.ALL,
        1: SubscriptionFilter.ACTIVE,
        2: SubscriptionFilter.PAUSED,
        3: SubscriptionFilter.COMPLETED,
    }
    selected_filter = filters.get(callback_data.filter_code, SubscriptionFilter.ALL)

    async def operation(identity: TelegramIdentity) -> SubscriptionResult:
        if callback_data.action in {"page", "filter"}:
            return await subscription_query_service.list(
                identity,
                selected_filter=selected_filter,
                page=callback_data.page,
                expected_version=(callback_data.version if callback_data.version != 0 else None),
            )
        if callback_data.action == "details":
            return await subscription_query_service.details(
                identity,
                callback_data.subscription_id,
            )
        if callback_data.action == "check":
            return await subscription_query_service.check_now(
                identity,
                callback_data.subscription_id,
            )
        return await subscription_query_service.future_action(
            identity,
            callback_data.subscription_id,
        )

    await _edit(callback, operation)


async def _answer(
    message: Message,
    operation: Callable[[TelegramIdentity], Awaitable[SubscriptionResult]],
) -> None:
    if message.from_user is None:
        return
    result = await operation(identity_from_telegram(message.from_user, message.chat.id))
    rendered = render_subscriptions(result)
    await message.answer(rendered.text, reply_markup=rendered.reply_markup)


async def _edit(
    callback: CallbackQuery,
    operation: Callable[[TelegramIdentity], Awaitable[SubscriptionResult]],
) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        await callback.answer(
            "Кнопка устарела. Откройте личный чат и отправьте /subscriptions.",
            show_alert=True,
        )
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Подписки доступны только в личном чате с ботом.",
            show_alert=True,
        )
        return
    result = await operation(identity_from_telegram(callback.from_user, callback.message.chat.id))
    rendered = render_subscriptions(result)
    await callback.message.edit_text(rendered.text, reply_markup=rendered.reply_markup)
    await callback.answer()
