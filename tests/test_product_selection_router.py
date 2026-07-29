from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User

from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.application.product_selection import ProductSelectionService
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.product_selection import (
    DiscoveryResponse,
    DiscoveryStatus,
    ProductCandidate,
    ProductInputMode,
)
from pharmacy_bot.infrastructure.product_discovery import ConfiguredProductLinkPolicy
from pharmacy_bot.presentation.callbacks import ProductCallback
from pharmacy_bot.presentation.product_selection_router import (
    cancel_product_selection_by_command,
    handle_product_callback,
    handle_product_text,
    start_product_selection_by_callback,
    start_product_selection_by_command,
)
from tests.fakes import InMemoryOnboardingRepository
from tests.product_fakes import (
    FakeProductDiscoveryGateway,
    InMemoryProductDraftRepository,
)


@dataclass(frozen=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def telegram_user() -> User:
    return User(id=1001, is_bot=False, first_name="Test", language_code="ru")


def identity() -> TelegramIdentity:
    return TelegramIdentity(telegram_user_id=1001, telegram_chat_id=1001)


def services() -> tuple[OnboardingService, ProductSelectionService]:
    onboarding = OnboardingService(
        InMemoryOnboardingRepository(),
        DocumentBundle(
            terms_version="terms-v1",
            terms_url="https://example.com/terms",
            privacy_version="privacy-v1",
            privacy_url="https://example.com/privacy",
        ),
    )
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(
            DiscoveryStatus.SUCCESS,
            (
                ProductCandidate(
                    candidate_key="product-1",
                    version="v1",
                    name="Товар",
                    dosage="10 мг",
                ),
            ),
        )
    )
    service = ProductSelectionService(
        onboarding,
        InMemoryProductDraftRepository(),
        discovery,
        ConfiguredProductLinkPolicy(("shop.example",)),
        query_min_length=2,
        query_max_length=160,
        url_max_length=2048,
        page_size=5,
        draft_ttl=timedelta(hours=1),
        clock=FixedClock(datetime(2026, 7, 29, 12, 0, tzinfo=UTC)),
    )
    return onboarding, service


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


async def test_command_and_menu_callback_open_the_same_server_draft() -> None:
    onboarding, service = services()
    await onboarding.accept(identity())
    command_message = private_message("/add")

    await start_product_selection_by_command(command_message, service)
    callback, callback_message = private_callback()
    await start_product_selection_by_callback(callback, service)

    assert "Выберите поиск по названию" in command_message.answer.await_args.args[0]
    assert "Выберите поиск по названию" in callback_message.edit_text.await_args.args[0]
    callback.answer.assert_awaited_once_with()


async def test_text_input_shows_progress_then_current_results() -> None:
    onboarding, service = services()
    await onboarding.accept(identity())
    started = await service.start(identity())
    assert started.draft is not None
    await service.choose_input(
        identity(),
        ProductInputMode.SEARCH,
        generation=started.draft.generation,
    )
    message = private_message("товар 10 мг")
    progress = Mock(spec=Message)
    progress.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=progress)

    await handle_product_text(message, service)

    message.answer.assert_awaited_once_with("Проверяю запрос и актуальность результатов…")
    assert "Результаты для: товар 10 мг" in progress.edit_text.await_args.args[0]


async def test_old_callback_returns_stale_view_without_selecting_product() -> None:
    onboarding, service = services()
    await onboarding.accept(identity())
    started = await service.start(identity())
    assert started.draft is not None
    callback, callback_message = private_callback()

    await handle_product_callback(
        callback,
        ProductCallback(action="select", generation=999, value=0),
        service,
    )

    assert "устаревшей или изменившейся выдаче" in (callback_message.edit_text.await_args.args[0])


async def test_cancel_command_removes_draft_results_without_subscription() -> None:
    onboarding, service = services()
    await onboarding.accept(identity())
    await service.start(identity())
    message = private_message("/cancel")

    await cancel_product_selection_by_command(message, service)

    assert "подписка и мониторинг не созданы" in message.answer.await_args.args[0]


async def test_product_callback_in_group_does_not_read_or_change_draft() -> None:
    _, service = services()
    message = Mock(spec=Message)
    message.chat = Chat(id=-1001, type=ChatType.GROUP)
    message.edit_text = AsyncMock()
    callback = Mock(spec=CallbackQuery)
    callback.from_user = telegram_user()
    callback.message = message
    callback.answer = AsyncMock()

    await handle_product_callback(
        callback,
        ProductCallback(action="mode_search", generation=1, value=0),
        service,
    )

    message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once_with(
        "Выбор товара доступен только в личном чате с ботом.",
        show_alert=True,
    )
