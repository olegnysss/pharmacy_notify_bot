from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import TelegramObject, Update

from pharmacy_bot.application.localization import MessageKey, Translator
from pharmacy_bot.domain.dialog import UpdateClaim

logger = logging.getLogger(__name__)


class UpdateReceiptRepository(Protocol):
    async def claim(
        self,
        update_id: int,
        *,
        now: datetime,
        lease: timedelta,
    ) -> UpdateClaim: ...

    async def complete(self, update_id: int, *, now: datetime) -> None: ...

    async def fail(
        self,
        update_id: int,
        *,
        now: datetime,
        correlation_id: str,
    ) -> None: ...


class ReliableUpdateMiddleware(BaseMiddleware):
    def __init__(
        self,
        repository: UpdateReceiptRepository,
        translator: Translator,
        *,
        lease: timedelta = timedelta(minutes=2),
    ) -> None:
        self._repository = repository
        self._translator = translator
        self._lease = lease

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        now = datetime.now(UTC)
        claim = await self._repository.claim(
            event.update_id,
            now=now,
            lease=self._lease,
        )
        language = self._language(event)
        if claim is UpdateClaim.COMPLETED:
            await self._safe_answer(
                event,
                self._translator.text(MessageKey.DUPLICATE_UPDATE, language),
            )
            return None
        if claim is UpdateClaim.IN_PROGRESS:
            await self._safe_answer(
                event,
                self._translator.text(MessageKey.UPDATE_IN_PROGRESS, language),
            )
            return None
        try:
            result = await handler(event, data)
        except Exception:
            correlation_id = str(uuid4())
            logger.exception(
                "Unhandled Telegram update error correlation_id=%s update_id=%s",
                correlation_id,
                event.update_id,
            )
            await self._repository.fail(
                event.update_id,
                now=datetime.now(UTC),
                correlation_id=correlation_id,
            )
            await self._safe_answer(
                event,
                self._translator.text(
                    MessageKey.INTERNAL_ERROR,
                    language,
                    correlation_id=correlation_id,
                ),
            )
            return None
        await self._repository.complete(event.update_id, now=datetime.now(UTC))
        return result

    @staticmethod
    def _language(event: Update) -> str | None:
        if event.message and event.message.from_user:
            return event.message.from_user.language_code
        if event.callback_query:
            return event.callback_query.from_user.language_code
        return None

    @staticmethod
    async def _answer(event: Update, text: str) -> None:
        if event.callback_query:
            await event.callback_query.answer(text, show_alert=True)
        elif event.message:
            await event.message.answer(text)

    @classmethod
    async def _safe_answer(cls, event: Update, text: str) -> None:
        try:
            await cls._answer(event, text)
        except TelegramAPIError:
            logger.info("Could not deliver safe update response update_id=%s", event.update_id)
