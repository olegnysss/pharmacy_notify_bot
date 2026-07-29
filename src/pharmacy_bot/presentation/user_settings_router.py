from __future__ import annotations

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from pharmacy_bot.application.user_settings import SettingsResult, UserSettingsService
from pharmacy_bot.domain.subscription_setup import LocationInputMode
from pharmacy_bot.domain.user_settings import SupportedLanguage
from pharmacy_bot.presentation.callbacks import NavigationCallback, SettingsCallback
from pharmacy_bot.presentation.onboarding_router import identity_from_telegram
from pharmacy_bot.presentation.user_settings_rendering import render_user_settings

router = Router(name=__name__)


@router.message(Command("settings", "location"), F.chat.type == ChatType.PRIVATE)
async def open_settings_command(
    message: Message,
    user_settings_service: UserSettingsService,
) -> None:
    if message.from_user is None or message.text is None:
        return
    command = message.text.split(maxsplit=1)[0].removeprefix("/").split("@", 1)[0]
    result = await user_settings_service.open(
        identity_from_telegram(message.from_user, message.chat.id),
        location_only=command == "location",
    )
    await _answer(message, result)


@router.callback_query(
    NavigationCallback.filter(F.action.in_({"settings", "location"})),
)
async def open_settings_callback(
    callback: CallbackQuery,
    callback_data: NavigationCallback,
    user_settings_service: UserSettingsService,
) -> None:
    await _edit(
        callback,
        await _open_from_callback(callback, callback_data, user_settings_service),
    )


async def _open_from_callback(
    callback: CallbackQuery,
    callback_data: NavigationCallback,
    service: UserSettingsService,
) -> SettingsResult | None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        return None
    return await service.open(
        identity_from_telegram(callback.from_user, callback.message.chat.id),
        location_only=callback_data.action == "location",
    )


@router.callback_query(SettingsCallback.filter())
async def update_settings_callback(
    callback: CallbackQuery,
    callback_data: SettingsCallback,
    user_settings_service: UserSettingsService,
) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        await _edit(callback, None)
        return
    identity = identity_from_telegram(callback.from_user, callback.message.chat.id)
    action = callback_data.action
    generation = callback_data.generation
    value = callback_data.value
    result: SettingsResult
    if action == "section":
        result = await user_settings_service.show_section(identity, generation, value)
    elif action == "location_mode":
        modes = {
            1: LocationInputMode.CITY,
            2: LocationInputMode.ADDRESS,
            3: LocationInputMode.COORDINATES,
        }
        mode = modes.get(value)
        result = (
            await user_settings_service.choose_location_mode(identity, generation, mode)
            if mode
            else await user_settings_service.show_section(identity, -1, 1)
        )
    elif action == "select_location":
        result = await user_settings_service.select_location(identity, generation, value)
    elif action == "radius":
        result = await user_settings_service.set_radius(identity, generation, value)
    elif action == "source":
        result = await user_settings_service.toggle_source(identity, generation, value)
    elif action == "sources_done":
        result = await user_settings_service.finish_sources(identity, generation)
    elif action == "clear":
        result = await user_settings_service.clear_defaults(identity, generation)
    elif action == "language":
        result = await user_settings_service.set_language(
            identity,
            generation,
            SupportedLanguage.RU,
        )
    elif action == "timezone":
        result = await user_settings_service.set_timezone(identity, generation, value)
    elif action == "notify":
        result = await user_settings_service.update_notifications(identity, generation, value)
    else:
        result = await user_settings_service.show_section(identity, -1, 6)
    await _edit(callback, result)


@router.message(F.chat.type == ChatType.PRIVATE, F.location)
async def settings_coordinates(
    message: Message,
    user_settings_service: UserSettingsService,
) -> None:
    if message.from_user is None or message.location is None:
        raise SkipHandler
    result = await user_settings_service.submit_coordinates(
        identity_from_telegram(message.from_user, message.chat.id),
        message.location.latitude,
        message.location.longitude,
    )
    if result is None:
        raise SkipHandler
    await _answer(message, result)


@router.message(F.chat.type == ChatType.PRIVATE, F.text, ~F.text.startswith("/"))
async def settings_text(
    message: Message,
    user_settings_service: UserSettingsService,
) -> None:
    if message.from_user is None or message.text is None:
        raise SkipHandler
    identity = identity_from_telegram(message.from_user, message.chat.id)
    if not await user_settings_service.accepts_text(identity):
        raise SkipHandler
    result = await user_settings_service.submit_text(identity, message.text)
    if result is None:
        raise SkipHandler
    await _answer(message, result)


async def _answer(message: Message, result: SettingsResult) -> None:
    rendered = render_user_settings(result)
    await message.answer(rendered.text, reply_markup=rendered.reply_markup)


async def _edit(callback: CallbackQuery, result: SettingsResult | None) -> None:
    if result is None or not isinstance(callback.message, Message):
        await callback.answer(
            "Кнопка устарела. Откройте личный чат и отправьте /settings.",
            show_alert=True,
        )
        return
    rendered = render_user_settings(result)
    await callback.message.edit_text(rendered.text, reply_markup=rendered.reply_markup)
    await callback.answer()
