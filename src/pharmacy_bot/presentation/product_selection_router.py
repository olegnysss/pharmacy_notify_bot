from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from pharmacy_bot.application.product_selection import (
    ProductSelectionResult,
    ProductSelectionService,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.product_selection import ProductInputMode
from pharmacy_bot.presentation.callbacks import ProductCallback, SubscriptionCallback
from pharmacy_bot.presentation.onboarding_router import identity_from_telegram
from pharmacy_bot.presentation.product_selection_rendering import render_product_selection

router = Router(name=__name__)


@router.message(Command("add"), F.chat.type == ChatType.PRIVATE)
async def start_product_selection_by_command(
    message: Message,
    product_selection_service: ProductSelectionService,
) -> None:
    await _answer(message, product_selection_service.start)


@router.message(Command("cancel"), F.chat.type == ChatType.PRIVATE)
async def cancel_product_selection_by_command(
    message: Message,
    product_selection_service: ProductSelectionService,
) -> None:
    await _answer(message, product_selection_service.cancel)


@router.callback_query(SubscriptionCallback.filter(F.action == "start"))
async def start_product_selection_by_callback(
    callback: CallbackQuery,
    product_selection_service: ProductSelectionService,
) -> None:
    await _edit(callback, product_selection_service.start)


@router.callback_query(ProductCallback.filter())
async def handle_product_callback(
    callback: CallbackQuery,
    callback_data: ProductCallback,
    product_selection_service: ProductSelectionService,
) -> None:
    action = callback_data.action

    async def operation(identity: TelegramIdentity) -> ProductSelectionResult:
        if action == "mode_search":
            return await product_selection_service.choose_input(
                identity,
                ProductInputMode.SEARCH,
                generation=callback_data.generation,
            )
        if action == "mode_link":
            return await product_selection_service.choose_input(
                identity,
                ProductInputMode.LINK,
                generation=callback_data.generation,
            )
        if action == "methods":
            return await product_selection_service.choose_methods(
                identity,
                generation=callback_data.generation,
            )
        if action == "page":
            return await product_selection_service.show_page(
                identity,
                generation=callback_data.generation,
                page=callback_data.value,
            )
        if action == "select":
            return await product_selection_service.select_candidate(
                identity,
                generation=callback_data.generation,
                ordinal=callback_data.value,
            )
        if action == "results":
            return await product_selection_service.show_results(
                identity,
                generation=callback_data.generation,
            )
        if action == "confirm":
            return await product_selection_service.confirm_candidate(
                identity,
                generation=callback_data.generation,
                ordinal=callback_data.value,
            )
        if action == "cancel":
            return await product_selection_service.cancel(
                identity,
                generation=callback_data.generation,
            )
        return await product_selection_service.show_page(
            identity,
            generation=-1,
            page=0,
        )

    await _edit(callback, operation)


@router.message(
    F.chat.type == ChatType.PRIVATE,
    F.text,
    ~F.text.startswith("/"),
)
async def handle_product_text(
    message: Message,
    product_selection_service: ProductSelectionService,
) -> None:
    if message.from_user is None or message.text is None:
        raise SkipHandler
    identity = identity_from_telegram(message.from_user, message.chat.id)
    if not await product_selection_service.accepts_text(identity):
        raise SkipHandler

    progress = await message.answer("Проверяю запрос и актуальность результатов…")
    result = await product_selection_service.submit_text(identity, message.text)
    if result is None:
        await progress.edit_text("Сценарий уже изменился. Отправьте /add, чтобы продолжить.")
        return
    rendered = render_product_selection(result)
    await progress.edit_text(rendered.text, reply_markup=rendered.reply_markup)


async def _answer(
    message: Message,
    operation: Callable[[TelegramIdentity], Awaitable[ProductSelectionResult]],
) -> None:
    if message.from_user is None:
        return
    result = await operation(identity_from_telegram(message.from_user, message.chat.id))
    rendered = render_product_selection(result)
    await message.answer(rendered.text, reply_markup=rendered.reply_markup)


async def _edit(
    callback: CallbackQuery,
    operation: Callable[[TelegramIdentity], Awaitable[ProductSelectionResult]],
) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        await callback.answer(
            "Кнопка устарела. Откройте личный чат и отправьте /add.",
            show_alert=True,
        )
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Выбор товара доступен только в личном чате с ботом.",
            show_alert=True,
        )
        return

    result = await operation(identity_from_telegram(callback.from_user, callback.message.chat.id))
    rendered = render_product_selection(result)
    await callback.message.edit_text(rendered.text, reply_markup=rendered.reply_markup)
    await callback.answer()
