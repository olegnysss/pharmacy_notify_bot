from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pharmacy_bot.application.subscriptions import (
    SubscriptionFilter,
    SubscriptionResult,
    SubscriptionView,
)
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    CompletionMode,
    Subscription,
    SubscriptionStatus,
)
from pharmacy_bot.presentation.callbacks import (
    NavigationCallback,
    SubscriptionCallback,
    SubscriptionViewCallback,
)
from pharmacy_bot.presentation.rendering import RenderedMessage, render_onboarding


def render_subscriptions(result: SubscriptionResult) -> RenderedMessage:
    if result.view is SubscriptionView.ONBOARDING:
        return render_onboarding(result.onboarding)
    if result.view is SubscriptionView.LIST:
        return _list(result)
    if result.view is SubscriptionView.DETAILS:
        return _details(result)
    if result.view is SubscriptionView.NOT_FOUND:
        return _not_found()
    if result.view is SubscriptionView.STALE:
        return _stale(result)
    if result.view is SubscriptionView.ACTION_UNAVAILABLE:
        return _future_action(result)
    return _check_result(result)


def _list(result: SubscriptionResult) -> RenderedMessage:
    page = result.page
    if page is None:
        return _not_found()
    filter_code = _filter_code(result.selected_filter)
    if not page.items:
        return RenderedMessage(
            (
                "Подписок с выбранным статусом пока нет.\n\n"
                "Создайте точное правило мониторинга или выберите другой фильтр."
            ),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    _filter_row(result.selected_filter, page.version),
                    [
                        InlineKeyboardButton(
                            text="Добавить товар",
                            callback_data=SubscriptionCallback(action="start").pack(),
                        )
                    ],
                    [_main_button()],
                ]
            ),
        )
    lines = [
        f"Мои подписки — {_filter_label(result.selected_filter)}",
        f"Страница {page.page + 1} из {page.total_pages}, всего {page.total_items}",
    ]
    rows: list[list[InlineKeyboardButton]] = [_filter_row(result.selected_filter, page.version)]
    for subscription in page.items:
        lines.extend(
            [
                "",
                (
                    f"#{subscription.id} {_status_icon(subscription.status)} "
                    f"{_clip(subscription.product.name, 120)}"
                ),
                (
                    f"   {_clip(subscription.location.display_name, 100)} · "
                    f"{_state_short(subscription)}"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Открыть #{subscription.id}",
                    callback_data=_callback(
                        "details",
                        subscription.id,
                        page.page,
                        filter_code,
                        page.version,
                    ),
                )
            ]
        )
    pagination: list[InlineKeyboardButton] = []
    if page.page > 0:
        pagination.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=_callback(
                    "page",
                    0,
                    page.page - 1,
                    filter_code,
                    page.version,
                ),
            )
        )
    if page.page + 1 < page.total_pages:
        pagination.append(
            InlineKeyboardButton(
                text="Вперёд →",
                callback_data=_callback(
                    "page",
                    0,
                    page.page + 1,
                    filter_code,
                    page.version,
                ),
            )
        )
    if pagination:
        rows.append(pagination)
    rows.append([_main_button()])
    return RenderedMessage("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _details(result: SubscriptionResult, prefix: str | None = None) -> RenderedMessage:
    subscription = result.subscription
    if subscription is None:
        return _not_found()
    lines = []
    if prefix:
        lines.extend([prefix, ""])
    lines.extend(
        [
            f"Подписка #{subscription.id}",
            "",
            f"Товар: {_product(subscription)}",
            f"Локация: {subscription.location.display_name}",
            f"Радиус: {subscription.radius_meters / 1000:g} км",
            f"Источники: {', '.join(subscription.source_codes)}",
            f"Фильтры: {_filters(subscription)}",
            f"Режим: {_completion(subscription)}",
            f"Статус подписки: {_status_label(subscription.status)}",
            f"Создана: {_date(subscription.created_at)}",
            "",
            f"Текущее состояние: {_state_label(subscription)}",
            f"Актуальность: {_freshness(subscription)}",
        ]
    )
    if subscription.state_source_name:
        lines.append(f"Подтверждающий источник: {subscription.state_source_name}")
    if subscription.has_partial_source_error:
        lines.append(
            "Часть источников сейчас недоступна; подтверждённые данные остальных сохранены."
        )
    rows: list[list[InlineKeyboardButton]] = []
    if subscription.status is SubscriptionStatus.ACTIVE:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Проверить сейчас",
                    callback_data=_callback("check", subscription.id),
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Изменить",
                    callback_data=_callback("edit", subscription.id),
                ),
                InlineKeyboardButton(
                    text=(
                        "Возобновить"
                        if subscription.status is SubscriptionStatus.PAUSED
                        else "Приостановить"
                    ),
                    callback_data=_callback("lifecycle", subscription.id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Удалить",
                    callback_data=_callback("delete", subscription.id),
                )
            ],
            [
                InlineKeyboardButton(
                    text="К списку",
                    callback_data=NavigationCallback(action="subscriptions").pack(),
                ),
                _main_button(),
            ],
        ]
    )
    return RenderedMessage("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _check_result(result: SubscriptionResult) -> RenderedMessage:
    messages = {
        SubscriptionView.CHECK_CACHED: (
            "Использованы свежие сохранённые данные; внешний запрос не потребовался."
        ),
        SubscriptionView.CHECK_QUEUED: (
            "Проверка поставлена в очередь. Повторное нажатие не создаст параллельный запрос."
        ),
        SubscriptionView.CHECK_IN_PROGRESS: "Проверка уже выполняется. Дождитесь результата.",
        SubscriptionView.CHECK_RATE_LIMITED: (
            f"Лимит ручной проверки. Следующая попытка после {_date(result.retry_at)}."
        ),
        SubscriptionView.CHECK_ERROR: (
            "Не удалось поставить проверку в очередь. Последнее состояние не изменено "
            "и не считается отсутствием."
        ),
    }
    return _details(result, messages[result.view])


def _future_action(result: SubscriptionResult) -> RenderedMessage:
    return _details(
        result,
        (
            "Это действие будет реализовано в story жизненного цикла подписки. "
            "Текущая конфигурация не изменилась."
        ),
    )


def _not_found() -> RenderedMessage:
    return RenderedMessage(
        (
            "Подписка недоступна или больше не существует. "
            "Бот не раскрывает сведения о чужих объектах."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Мои подписки",
                        callback_data=NavigationCallback(action="subscriptions").pack(),
                    )
                ],
                [_main_button()],
            ]
        ),
    )


def _stale(result: SubscriptionResult) -> RenderedMessage:
    return RenderedMessage(
        "Список изменился после открытия этой страницы. Показана безопасная ссылка на начало.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Обновить список",
                        callback_data=_callback(
                            "page",
                            0,
                            0,
                            _filter_code(result.selected_filter),
                            0,
                        ),
                    )
                ],
                [_main_button()],
            ]
        ),
    )


def _filter_row(
    selected: SubscriptionFilter,
    version: int,
) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=("• " if selected is item else "") + label,
            callback_data=_callback("filter", 0, 0, _filter_code(item), version),
        )
        for item, label in (
            (SubscriptionFilter.ALL, "Все"),
            (SubscriptionFilter.ACTIVE, "Активные"),
            (SubscriptionFilter.PAUSED, "Пауза"),
        )
    ]


def _callback(
    action: str,
    subscription_id: int,
    page: int = 0,
    filter_code: int = 0,
    version: int = 0,
) -> str:
    return SubscriptionViewCallback(
        action=action,
        subscription_id=subscription_id,
        page=page,
        filter_code=filter_code,
        version=version,
    ).pack()


def _main_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="Главное меню",
        callback_data=NavigationCallback(action="main").pack(),
    )


def _filter_code(value: SubscriptionFilter) -> int:
    return {
        SubscriptionFilter.ALL: 0,
        SubscriptionFilter.ACTIVE: 1,
        SubscriptionFilter.PAUSED: 2,
        SubscriptionFilter.COMPLETED: 3,
    }[value]


def _filter_label(value: SubscriptionFilter) -> str:
    return {
        SubscriptionFilter.ALL: "все",
        SubscriptionFilter.ACTIVE: "активные",
        SubscriptionFilter.PAUSED: "приостановленные",
        SubscriptionFilter.COMPLETED: "завершённые",
    }[value]


def _status_icon(value: SubscriptionStatus) -> str:
    return {
        SubscriptionStatus.ACTIVE: "🟢",
        SubscriptionStatus.PAUSED: "⏸",
        SubscriptionStatus.COMPLETED: "✅",
        SubscriptionStatus.DELETED: "🗑",
    }[value]


def _status_label(value: SubscriptionStatus) -> str:
    return {
        SubscriptionStatus.ACTIVE: "активна",
        SubscriptionStatus.PAUSED: "приостановлена",
        SubscriptionStatus.COMPLETED: "завершена",
        SubscriptionStatus.DELETED: "удалена",
    }[value]


def _state_short(subscription: Subscription) -> str:
    return {
        AvailabilityState.PENDING: "ожидает проверки",
        AvailabilityState.UNKNOWN: "неизвестно",
        AvailabilityState.UNAVAILABLE: "нет подтверждённого наличия",
        AvailabilityState.AVAILABLE: "есть подтверждённое наличие",
        AvailabilityState.LOW_STOCK: "мало",
        AvailabilityState.ORDERABLE: "доступно для заказа",
        AvailabilityState.SOURCE_ERROR: "ошибка источника",
        AvailabilityState.STALE: "данные устарели",
    }[subscription.availability_state]


def _state_label(subscription: Subscription) -> str:
    state = _state_short(subscription)
    if subscription.availability_state is AvailabilityState.SOURCE_ERROR:
        return f"{state}; это не означает отсутствие"
    if subscription.availability_state is AvailabilityState.UNKNOWN:
        return f"{state}; достоверных данных пока нет"
    if subscription.availability_state is AvailabilityState.STALE:
        return f"{state}; перепроверьте перед поездкой"
    return state


def _freshness(subscription: Subscription) -> str:
    if subscription.last_successful_check_at is None:
        return "успешных проверок ещё не было"
    if (
        subscription.freshness_expires_at is not None
        and subscription.freshness_expires_at <= datetime.now(UTC)
    ):
        return (
            f"устарело; последняя успешная проверка {_date(subscription.last_successful_check_at)}"
        )
    return f"последняя успешная проверка {_date(subscription.last_successful_check_at)}"


def _product(subscription: Subscription) -> str:
    values = [
        subscription.product.name,
        subscription.product.form,
        subscription.product.dosage,
        subscription.product.package,
        subscription.product.manufacturer,
    ]
    return _clip(" · ".join(value for value in values if value), 700)


def _filters(subscription: Subscription) -> str:
    values = []
    if subscription.filters.notify_low_stock:
        values.append("low_stock")
    if subscription.filters.notify_orderable:
        values.append("internet order")
    if subscription.filters.include_price:
        values.append("цена")
    return ", ".join(values) if values else "только подтверждённое наличие"


def _completion(subscription: Subscription) -> str:
    return {
        CompletionMode.CONTINUE: "продолжать",
        CompletionMode.PAUSE_AFTER_SUCCESS: "пауза после успеха",
        CompletionMode.COMPLETE_AFTER_SUCCESS: "завершить после успеха",
        CompletionMode.UNTIL_DATE: f"до {_date(subscription.ends_at)}",
    }[subscription.completion_mode]


def _date(value: datetime | None) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC") if value else "неизвестно"


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1]}…"
