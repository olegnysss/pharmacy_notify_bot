from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from aiogram.types import CallbackQuery, Chat, Message, User

from pharmacy_bot.application.onboarding import (
    DocumentBundle,
    OnboardingService,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.presentation.onboarding_router import (
    accept_documents,
    identity_from_telegram,
    start_onboarding,
)
from tests.fakes import InMemoryOnboardingRepository


def telegram_user() -> User:
    return User(id=1001, is_bot=False, first_name="Test", language_code="ru")


def test_identity_uses_server_side_telegram_ids() -> None:
    identity = identity_from_telegram(telegram_user(), chat_id=2002)

    assert identity == TelegramIdentity(
        telegram_user_id=1001,
        telegram_chat_id=2002,
        language_code="ru",
    )


async def test_start_handler_sends_rendered_welcome() -> None:
    repository = InMemoryOnboardingRepository()
    service = OnboardingService(
        repository,
        DocumentBundle(
            "terms-v1",
            "https://example.com/terms",
            "privacy-v1",
            "https://example.com/privacy",
        ),
    )
    message = Mock(spec=Message)
    message.from_user = telegram_user()
    message.chat = Chat(id=1001, type="private")
    message.answer = AsyncMock()

    await start_onboarding(message, service)

    message.answer.assert_awaited_once()
    assert "не гарантирует" in message.answer.await_args.args[0]


async def test_accept_callback_edits_message_only_after_persistence() -> None:
    repository = InMemoryOnboardingRepository()
    service = OnboardingService(
        repository,
        DocumentBundle(
            "terms-v1",
            "https://example.com/terms",
            "privacy-v1",
            "https://example.com/privacy",
        ),
    )
    message = Mock(spec=Message)
    message.chat = Chat(id=1001, type="private")
    message.edit_text = AsyncMock()
    callback = Mock(spec=CallbackQuery)
    callback.from_user = telegram_user()
    callback.message = message
    callback.answer = AsyncMock()

    await accept_documents(callback, service)

    assert repository.decision_details
    message.edit_text.assert_awaited_once()
    callback.answer.assert_awaited_once_with()


async def test_onboarding_callback_in_group_never_reads_or_updates_profile() -> None:
    repository = InMemoryOnboardingRepository()
    service = OnboardingService(
        repository,
        DocumentBundle(
            "terms-v1",
            "https://example.com/terms",
            "privacy-v1",
            "https://example.com/privacy",
        ),
    )
    message = Mock(spec=Message)
    message.chat = Chat(id=-1001, type="group")
    message.edit_text = AsyncMock()
    callback = Mock(spec=CallbackQuery)
    callback.from_user = telegram_user()
    callback.message = message
    callback.answer = AsyncMock()

    await accept_documents(callback, service)

    assert not repository.users
    message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once_with(
        "Управление доступно только в личном чате с ботом.",
        show_alert=True,
    )
