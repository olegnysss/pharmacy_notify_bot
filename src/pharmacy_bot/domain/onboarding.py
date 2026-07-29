from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OnboardingStatus(StrEnum):
    NEW = "new"
    AWAITING_CONSENT = "awaiting_consent"
    COMPLETED = "completed"
    DECLINED = "declined"


class ConsentMethod(StrEnum):
    TELEGRAM_INLINE_BUTTON = "telegram_inline_button"


class ConsentDecision(StrEnum):
    ACCEPTED = "accepted"
    DECLINED = "declined"


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    telegram_user_id: int
    telegram_chat_id: int
    language_code: str | None = None


@dataclass(frozen=True, slots=True)
class UserSnapshot:
    id: int
    identity: TelegramIdentity
    status: OnboardingStatus
