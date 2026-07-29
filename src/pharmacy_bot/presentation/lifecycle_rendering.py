from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pharmacy_bot.application.subscription_lifecycle import (
    LifecycleResult,
    LifecycleView,
)
from pharmacy_bot.domain.subscription_lifecycle import SubscriptionEditDraft
from pharmacy_bot.domain.subscription_setup import CompletionMode, LocationInputMode
from pharmacy_bot.presentation.callbacks import (
    LifecycleCallback,
    NavigationCallback,
    SubscriptionViewCallback,
)
from pharmacy_bot.presentation.rendering import RenderedMessage, render_onboarding


def render_lifecycle(result: LifecycleResult) -> RenderedMessage:
    if result.view is LifecycleView.ONBOARDING:
        return render_onboarding(result.onboarding)
    if result.view is LifecycleView.NOT_FOUND:
        return _simple(
            "Подписка недоступна или удалена. Сведения о чужих объектах не раскрываются."
        )
    if result.view is LifecycleView.CHOOSE_BLOCK:
        return _blocks(result)
    if result.view is LifecycleView.AWAITING_LOCATION:
        return _location_input(result)
    if result.view is LifecycleView.LOCATION_RESULTS:
        return _location_results(result)
    if result.view is LifecycleView.CHOOSE_RADIUS:
        return _radius(result)
    if result.view is LifecycleView.CHOOSE_SOURCES:
        return _sources(result)
    if result.view is LifecycleView.CHOOSE_FILTERS:
        return _filters(result)
    if result.view is LifecycleView.CHOOSE_COMPLETION:
        return _completion(result)
    if result.view is LifecycleView.AWAITING_END_DATE:
        return _end_date(result)
    if result.view is LifecycleView.REVIEW:
        return _review(result)
    if result.view is LifecycleView.DELETE_CONFIRM:
        return _delete_confirm(result)
    if result.view in {
        LifecycleView.APPLIED,
        LifecycleView.PAUSED,
        LifecycleView.RESUMED,
        LifecycleView.DELETED,
        LifecycleView.CANCELLED,
    }:
        return _success(result)
    if result.view is LifecycleView.INVALID_CONFIGURATION:
        return _invalid(result)
    if result.view in {LifecycleView.INPUT_ERROR, LifecycleView.TEMPORARY_ERROR}:
        return _error(result)
    return _simple(
        "Состояние подписки изменилось. Откройте актуальную карточку и повторите действие."
    )


def _blocks(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        (
            f"Изменение подписки #{draft.subscription_id}\n\n"
            f"Товар остаётся неизменным: {draft.original.product.name}.\n"
            "Для другого товара создайте новую подписку.\n\n"
            f"Локация: {draft.location.display_name}, {draft.radius_meters / 1000:g} км\n"
            f"Источники: {', '.join(draft.selected_source_codes)}\n"
            f"Фильтры: {_filter_text(draft)}\n"
            f"Режим: {_completion_text(draft)}"
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button("Локация", "block", draft, 1),
                    _button("Источники", "block", draft, 2),
                ],
                [
                    _button("Фильтры", "block", draft, 3),
                    _button("Режим", "block", draft, 4),
                ],
                [_button("Проверить изменения", "block", draft, 5)],
                [_button("Отменить редактирование", "cancel", draft)],
            ]
        ),
    )


def _location_input(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    if draft.location_mode is None:
        return RenderedMessage(
            "Выберите способ новой локации. Точные координаты необязательны.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _button("Город", "location_mode", draft, 1),
                        _button("Адрес", "location_mode", draft, 2),
                    ],
                    [_button("Геопозиция Telegram", "location_mode", draft, 3)],
                    [_button("Назад", "back", draft)],
                ]
            ),
        )
    prompt = {
        LocationInputMode.CITY: "Введите новый город.",
        LocationInputMode.ADDRESS: "Введите новый адрес.",
        LocationInputMode.COORDINATES: ("Отправьте новую геопозицию через скрепку Telegram."),
    }[draft.location_mode]
    return RenderedMessage(
        prompt,
        InlineKeyboardMarkup(inline_keyboard=[[_button("Другой способ", "block", draft, 1)]]),
    )


def _location_results(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    rows = []
    lines = ["Подтвердите найденную локацию:"]
    for item in draft.location_candidates:
        if item.ordinal is None:
            continue
        lines.append(f"\n{item.ordinal + 1}. {item.display_name}")
        rows.append(
            [_button(f"Выбрать {item.ordinal + 1}", "select_location", draft, item.ordinal)]
        )
    rows.append([_button("Ввести заново", "block", draft, 1)])
    return RenderedMessage("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _radius(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        f"Новая локация: {draft.location.display_name}\n\nВыберите радиус.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button("1 км", "radius", draft, 1000),
                    _button("3 км", "radius", draft, 3000),
                    _button("5 км", "radius", draft, 5000),
                ],
                [
                    _button("10 км", "radius", draft, 10000),
                    _button("25 км", "radius", draft, 25000),
                ],
                [_button("Назад", "block", draft, 1)],
            ]
        ),
    )


def _sources(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    rows = []
    lines = ["Выберите работающие источники:"]
    for item in draft.available_sources:
        mark = "✅" if item.code in draft.selected_source_codes else "⬜️"
        if not item.available:
            mark = "⛔️"
            lines.append(f"\n{item.name}: {item.unavailable_reason or 'недоступен'}")
        if item.ordinal is not None:
            rows.append([_button(f"{mark} {item.name}", "source", draft, item.ordinal)])
    rows.extend(
        [
            [_button("Сохранить выбор", "sources_done", draft)],
            [_button("Назад", "back", draft)],
        ]
    )
    return RenderedMessage("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _filters(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        "Настройте поддерживаемые дополнительные условия.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button(
                        _mark("Сообщать «мало»", draft.filters.notify_low_stock), "filter", draft, 1
                    )
                ],
                [
                    _button(
                        _mark("Интернет-заказ", draft.filters.notify_orderable),
                        "filter",
                        draft,
                        2,
                    )
                ],
                [
                    _button(
                        _mark("Показывать цену", draft.filters.include_price), "filter", draft, 3
                    )
                ],
                [_button("Сохранить фильтры", "filters_done", draft)],
                [_button("Назад", "back", draft)],
            ]
        ),
    )


def _completion(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        (
            f"Текущий режим: {_completion_text(draft)}\n\n"
            "Выберите новое поведение после подтверждённого события."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Продолжать", "completion", draft, 1)],
                [_button("Пауза после успеха", "completion", draft, 2)],
                [_button("Завершить после успеха", "completion", draft, 3)],
                [_button("До даты", "completion", draft, 4)],
                [_button("Назад", "back", draft)],
            ]
        ),
    )


def _end_date(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        "Введите будущую дату в формате ДД.ММ.ГГГГ (часовой пояс профиля).",
        InlineKeyboardMarkup(inline_keyboard=[[_button("Другой режим", "block", draft, 4)]]),
    )


def _review(result: LifecycleResult) -> RenderedMessage:
    draft = _draft(result)
    changes = []
    if draft.location.key != draft.original.location.key:
        changes.append(
            f"локация: {draft.original.location.display_name} → {draft.location.display_name}"
        )
    if draft.radius_meters != draft.original.radius_meters:
        changes.append(
            f"радиус: {draft.original.radius_meters / 1000:g} → {draft.radius_meters / 1000:g} км"
        )
    if set(draft.selected_source_codes) != set(draft.original.source_codes):
        changes.append("набор источников изменён")
    if draft.filters != draft.original.filters:
        changes.append("фильтры изменены")
    if (
        draft.completion_mode != draft.original.completion_mode
        or draft.ends_at != draft.original.ends_at
    ):
        changes.append(f"режим: {_completion_text(draft)}")
    text = "\n".join(f"• {item}" for item in changes) or "• смысловых изменений нет"
    return RenderedMessage(
        (
            f"Проверьте изменения подписки #{draft.subscription_id}\n\n{text}\n\n"
            "Изменение области сбросит устаревшее состояние в «неизвестно» и запланирует "
            "новую проверку. Товар не заменяется."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Применить изменения", "apply", draft)],
                [_button("Вернуться к блокам", "back", draft)],
                [_button("Отменить", "cancel", draft)],
            ]
        ),
    )


def _delete_confirm(result: LifecycleResult) -> RenderedMessage:
    subscription = result.subscription
    if subscription is None:
        return _simple("Подписка недоступна.")
    return RenderedMessage(
        (
            f"Удалить подписку #{subscription.id}?\n\n"
            f"Товар: {subscription.product.name}\n\n"
            "Удаление необратимо в пользовательском интерфейсе и прекращает мониторинг. "
            "Пауза сохранила бы возможность возобновления."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, удалить",
                        callback_data=LifecycleCallback(
                            action="delete_confirm",
                            subscription_id=subscription.id,
                            generation=result.version,
                            value=0,
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data=_details_callback(subscription.id),
                    )
                ],
            ]
        ),
    )


def _success(result: LifecycleResult) -> RenderedMessage:
    labels = {
        LifecycleView.APPLIED: "Изменения применены атомарно.",
        LifecycleView.PAUSED: "Подписка приостановлена. Плановые проверки прекращены.",
        LifecycleView.RESUMED: "Подписка возобновлена и ожидает актуализации.",
        LifecycleView.DELETED: "Подписка удалена и больше не участвует в мониторинге.",
        LifecycleView.CANCELLED: "Редактирование отменено. Исходная подписка не изменена.",
    }
    subscription = result.subscription
    rows = (
        [
            [
                InlineKeyboardButton(
                    text="Открыть карточку", callback_data=_details_callback(subscription.id)
                )
            ]
        ]
        if subscription and result.view is not LifecycleView.DELETED
        else []
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Мои подписки",
                callback_data=NavigationCallback(action="subscriptions").pack(),
            )
        ]
    )
    return RenderedMessage(
        labels[result.view],
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _invalid(result: LifecycleResult) -> RenderedMessage:
    subscription = result.subscription
    rows = []
    if subscription:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Изменить настройки",
                    callback_data=LifecycleCallback(
                        action="edit",
                        subscription_id=subscription.id,
                        generation=0,
                        value=0,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Мои подписки",
                callback_data=NavigationCallback(action="subscriptions").pack(),
            )
        ]
    )
    return RenderedMessage(
        (
            "Переход невозможен: конфигурация устарела, неполна или состояние подписки "
            "не допускает это действие. Ничего не изменено."
        ),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _error(result: LifecycleResult) -> RenderedMessage:
    return RenderedMessage(
        (
            f"Не удалось продолжить: {result.error or 'временная ошибка сервиса'}.\n\n"
            "Исходная подписка не изменена."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Мои подписки",
                        callback_data=NavigationCallback(action="subscriptions").pack(),
                    )
                ]
            ]
        ),
    )


def _simple(text: str) -> RenderedMessage:
    return RenderedMessage(
        text,
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Мои подписки",
                        callback_data=NavigationCallback(action="subscriptions").pack(),
                    )
                ]
            ]
        ),
    )


def _button(
    text: str,
    action: str,
    draft: SubscriptionEditDraft,
    value: int = 0,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=LifecycleCallback(
            action=action,
            subscription_id=draft.subscription_id,
            generation=draft.generation,
            value=value,
        ).pack(),
    )


def _details_callback(subscription_id: int) -> str:
    return SubscriptionViewCallback(
        action="details",
        subscription_id=subscription_id,
        page=0,
        filter_code=0,
        version=0,
    ).pack()


def _draft(result: LifecycleResult) -> SubscriptionEditDraft:
    if result.draft is None:
        raise ValueError("lifecycle edit view requires draft")
    return result.draft


def _mark(text: str, enabled: bool) -> str:
    return f"{'✅' if enabled else '⬜️'} {text}"


def _filter_text(draft: SubscriptionEditDraft) -> str:
    values = []
    if draft.filters.notify_low_stock:
        values.append("мало")
    if draft.filters.notify_orderable:
        values.append("интернет-заказ")
    if draft.filters.include_price:
        values.append("цена")
    return ", ".join(values) or "базовые"


def _completion_text(draft: SubscriptionEditDraft) -> str:
    return {
        CompletionMode.CONTINUE: "продолжать",
        CompletionMode.PAUSE_AFTER_SUCCESS: "пауза после успеха",
        CompletionMode.COMPLETE_AFTER_SUCCESS: "завершить после успеха",
        CompletionMode.UNTIL_DATE: (f"до {draft.ends_at:%d.%m.%Y}" if draft.ends_at else "до даты"),
    }[draft.completion_mode]
