from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from aiogram import Bot
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Chat, Message, User

from pharmacy_bot.application.navigation import NavigationService
from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.presentation.callbacks import NavigationCallback, SubscriptionCallback
from pharmacy_bot.presentation.navigation_router import (
    PRIVATE_COMMANDS,
    enter_subscription_flow,
    explain_private_chat_only,
    navigate_by_callback,
    navigate_by_command,
)
from tests.fakes import InMemoryOnboardingRepository


def telegram_user() -> User:
    return User(id=1001, is_bot=False, first_name="Test", language_code="ru")


def services() -> tuple[OnboardingService, NavigationService]:
    onboarding = OnboardingService(
        InMemoryOnboardingRepository(),
        DocumentBundle(
            terms_version="terms-v1",
            terms_url="https://example.com/terms",
            privacy_version="privacy-v1",
            privacy_url="https://example.com/privacy",
        ),
    )
    return onboarding, NavigationService(onboarding)


def private_message(text: str = "") -> Message:
    message = Mock(spec=Message)
    message.from_user = telegram_user()
    message.chat = Chat(id=1001, type=ChatType.PRIVATE)
    message.text = text
    message.answer = AsyncMock()
    return message


def private_callback() -> tuple[CallbackQuery, Message]:
    message = private_message()
    message.edit_text = AsyncMock()
    callback = Mock(spec=CallbackQuery)
    callback.from_user = telegram_user()
    callback.message = message
    callback.answer = AsyncMock()
    return callback, message


def test_private_command_menu_contains_the_documented_commands() -> None:
    assert {command.command for command in PRIVATE_COMMANDS} == {
        "start",
        "add",
        "subscriptions",
        "location",
        "settings",
        "help",
        "privacy",
        "cancel",
    }


async def test_protected_command_returns_to_onboarding_before_consent() -> None:
    _, navigation = services()
    message = private_message("/settings")
    state = AsyncMock(spec=FSMContext)

    await navigate_by_command(message, navigation, state)

    assert "Перед началом" in message.answer.await_args.args[0]
    state.clear.assert_not_awaited()


async def test_cancel_clears_temporary_state_and_returns_to_menu() -> None:
    onboarding, navigation = services()
    await onboarding.accept(
        identity=telegram_identity(),
    )
    message = private_message("/cancel")
    state = AsyncMock(spec=FSMContext)

    await navigate_by_command(message, navigation, state)

    state.clear.assert_awaited_once_with()
    assert "Текущий сценарий отменён" in message.answer.await_args.args[0]


async def test_menu_callback_and_command_use_the_same_navigation_result() -> None:
    onboarding, navigation = services()
    await onboarding.accept(telegram_identity())
    callback, callback_message = private_callback()
    state = AsyncMock(spec=FSMContext)

    await navigate_by_callback(
        callback,
        NavigationCallback(action="settings"),
        navigation,
        state,
    )

    assert "Пользовательские настройки" in callback_message.edit_text.await_args.args[0]
    callback.answer.assert_awaited_once_with()


async def test_subscription_callback_is_a_safe_future_story_entrypoint() -> None:
    onboarding, navigation = services()
    await onboarding.accept(telegram_identity())
    callback, callback_message = private_callback()

    await enter_subscription_flow(
        callback,
        SubscriptionCallback(action="start"),
        navigation,
    )

    assert "следующей story" in callback_message.edit_text.await_args.args[0]
    callback.answer.assert_awaited_once_with()


async def test_group_command_does_not_read_profile_and_links_to_private_chat() -> None:
    message = Mock(spec=Message)
    message.chat = Chat(id=-1001, type=ChatType.SUPERGROUP)
    message.answer = AsyncMock()
    bot = Mock(spec=Bot)
    bot.me = AsyncMock(
        return_value=User(
            id=42,
            is_bot=True,
            first_name="Pharmacy",
            username="pharmacy_test_bot",
        )
    )

    await explain_private_chat_only(message, bot)

    assert "только в личном чате" in message.answer.await_args.args[0]
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].url == "https://t.me/pharmacy_test_bot"


async def test_group_callback_never_renders_profile_data() -> None:
    _, navigation = services()
    message = Mock(spec=Message)
    message.chat = Chat(id=-1001, type=ChatType.GROUP)
    message.edit_text = AsyncMock()
    callback = Mock(spec=CallbackQuery)
    callback.from_user = telegram_user()
    callback.message = message
    callback.answer = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    await navigate_by_callback(
        callback,
        NavigationCallback(action="subscriptions"),
        navigation,
        state,
    )

    message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once_with(
        "Управление доступно только в личном чате с ботом.",
        show_alert=True,
    )


def telegram_identity() -> TelegramIdentity:
    return TelegramIdentity(telegram_user_id=1001, telegram_chat_id=1001)
