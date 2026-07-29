from __future__ import annotations

from enum import StrEnum


class MessageKey(StrEnum):
    DUPLICATE_UPDATE = "duplicate_update"
    UPDATE_IN_PROGRESS = "update_in_progress"
    INTERNAL_ERROR = "internal_error"
    RECOVERY_RESET = "recovery_reset"
    RECOVERY_ACTIVE = "recovery_active"


_MESSAGES: dict[str, dict[MessageKey, str]] = {
    "ru": {
        MessageKey.DUPLICATE_UPDATE: "Это действие уже обработано.",
        MessageKey.UPDATE_IN_PROGRESS: "Действие уже обрабатывается. Подождите немного.",
        MessageKey.INTERNAL_ERROR: (
            "Не удалось завершить действие из-за внутренней ошибки. "
            "Повторите позже или обратитесь в поддержку. Код: {correlation_id}"
        ),
        MessageKey.RECOVERY_RESET: (
            "Незавершённый сценарий устарел или имеет несовместимую версию и был безопасно "
            "сброшен. Ни одна подписка из него не создана."
        ),
        MessageKey.RECOVERY_ACTIVE: (
            "Найден незавершённый сценарий. Можно безопасно продолжить с последнего "
            "подтверждённого шага."
        ),
    },
    "en": {
        MessageKey.DUPLICATE_UPDATE: "This action has already been processed.",
        MessageKey.UPDATE_IN_PROGRESS: "This action is already being processed. Please wait.",
        MessageKey.INTERNAL_ERROR: (
            "The action could not be completed because of an internal error. "
            "Try again later or contact support. Code: {correlation_id}"
        ),
        MessageKey.RECOVERY_RESET: (
            "The unfinished flow expired or used an incompatible version and was safely reset. "
            "It did not create a subscription."
        ),
        MessageKey.RECOVERY_ACTIVE: (
            "An unfinished flow was found. You can safely continue from the last confirmed step."
        ),
    },
}


class Translator:
    def text(self, key: MessageKey, language_code: str | None, **values: str) -> str:
        language = (language_code or "ru").split("-", 1)[0].lower()
        catalog = _MESSAGES.get(language, _MESSAGES["ru"])
        return catalog[key].format_map(values)
