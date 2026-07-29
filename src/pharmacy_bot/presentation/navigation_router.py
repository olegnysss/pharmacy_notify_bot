from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from pharmacy_bot.application.navigation import NavigationService, NavigationTarget
from pharmacy_bot.presentation.callbacks import NavigationCallback, SubscriptionCallback
from pharmacy_bot.presentation.navigation_rendering import render_navigation
from pharmacy_bot.presentation.onboarding_router import identity_from_telegram

router = Router(name=__name__)

PRIVATE_COMMANDS = (
    BotCommand(command="start", description="Открыть главное меню"),
    BotCommand(command="add", description="Добавить товар"),
    BotCommand(command="subscriptions", description="Мои подписки"),
    BotCommand(command="location", description="Настроить локацию"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="privacy", description="Конфиденциальность"),
    BotCommand(command="cancel", description="Отменить текущий сценарий"),
)

COMMAND_TARGETS = {
    "add": NavigationTarget.ADD_SUBSCRIPTION,
    "subscriptions": NavigationTarget.SUBSCRIPTIONS,
    "location": NavigationTarget.LOCATION,
    "settings": NavigationTarget.SETTINGS,
    "help": NavigationTarget.HELP,
    "privacy": NavigationTarget.PRIVACY,
    "cancel": NavigationTarget.CANCEL,
}

CALLBACK_TARGETS = {
    "main": NavigationTarget.MAIN_MENU,
    "subscriptions": NavigationTarget.SUBSCRIPTIONS,
    "check": NavigationTarget.CHECK_AVAILABILITY,
    "location": NavigationTarget.LOCATION,
    "settings": NavigationTarget.SETTINGS,
    "help": NavigationTarget.HELP,
    "privacy": NavigationTarget.PRIVACY,
    "cancel": NavigationTarget.CANCEL,
}


async def configure_private_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        commands=list(PRIVATE_COMMANDS),
        scope=BotCommandScopeAllPrivateChats(),
    )


@router.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    F.text.startswith("/"),
)
async def explain_private_chat_only(message: Message, bot: Bot) -> None:
    me = await bot.me()
    private_url = f"https://t.me/{me.username}" if me.username else "https://t.me"
    await message.answer(
        "В группах бот показывает только нейтральную справку. "
        "Подписки, настройки и данные профиля доступны только в личном чате.\n\n"
        "Бот не гарантирует наличие товара и не даёт медицинских рекомендаций.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть личный чат", url=private_url)]]
        ),
    )


@router.message(
    Command(*COMMAND_TARGETS),
    F.chat.type == ChatType.PRIVATE,
)
async def navigate_by_command(
    message: Message,
    navigation_service: NavigationService,
    state: FSMContext,
) -> None:
    if message.from_user is None or message.text is None:
        return
    command = message.text.split(maxsplit=1)[0].removeprefix("/").split("@", maxsplit=1)[0]
    target = COMMAND_TARGETS[command]
    if target is NavigationTarget.CANCEL:
        await state.clear()
    await _answer_navigation(message, navigation_service, target)


@router.callback_query(NavigationCallback.filter())
async def navigate_by_callback(
    callback: CallbackQuery,
    callback_data: NavigationCallback,
    navigation_service: NavigationService,
    state: FSMContext,
) -> None:
    target = CALLBACK_TARGETS.get(callback_data.action, NavigationTarget.UNKNOWN)
    if target is NavigationTarget.CANCEL:
        await state.clear()
    await _edit_navigation(callback, navigation_service, target)


@router.callback_query(SubscriptionCallback.filter())
async def enter_subscription_flow(
    callback: CallbackQuery,
    callback_data: SubscriptionCallback,
    navigation_service: NavigationService,
) -> None:
    target = (
        NavigationTarget.ADD_SUBSCRIPTION
        if callback_data.action == "start"
        else NavigationTarget.UNKNOWN
    )
    await _edit_navigation(callback, navigation_service, target)


@router.message(F.chat.type == ChatType.PRIVATE)
async def handle_unknown_private_input(
    message: Message,
    navigation_service: NavigationService,
) -> None:
    if message.from_user is None:
        return
    await _answer_navigation(message, navigation_service, NavigationTarget.UNKNOWN)


async def _answer_navigation(
    message: Message,
    navigation_service: NavigationService,
    target: NavigationTarget,
) -> None:
    if message.from_user is None:
        return
    result = await navigation_service.navigate(
        identity_from_telegram(message.from_user, message.chat.id),
        target,
    )
    rendered = render_navigation(result)
    await message.answer(rendered.text, reply_markup=rendered.reply_markup)


async def _edit_navigation(
    callback: CallbackQuery,
    navigation_service: NavigationService,
    target: NavigationTarget,
) -> None:
    if callback.from_user is None or not isinstance(callback.message, Message):
        await callback.answer(
            "Кнопка устарела. Откройте личный чат и отправьте /start.",
            show_alert=True,
        )
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Управление доступно только в личном чате с ботом.",
            show_alert=True,
        )
        return

    result = await navigation_service.navigate(
        identity_from_telegram(callback.from_user, callback.message.chat.id),
        target,
    )
    rendered = render_navigation(result)
    await callback.message.edit_text(rendered.text, reply_markup=rendered.reply_markup)
    await callback.answer()
