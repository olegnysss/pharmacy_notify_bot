from __future__ import annotations

from datetime import UTC

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pharmacy_bot.application.subscription_setup import SetupResult, SetupView
from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    LocationInputMode,
    SetupStatus,
    SubscriptionSetupDraft,
)
from pharmacy_bot.presentation.callbacks import (
    NavigationCallback,
    SetupCallback,
    SubscriptionCallback,
)
from pharmacy_bot.presentation.rendering import RenderedMessage, main_menu_markup, render_onboarding


def render_subscription_setup(result: SetupResult) -> RenderedMessage:
    if result.view is SetupView.ONBOARDING:
        return render_onboarding(result.onboarding)
    if result.view is SetupView.PRODUCT_REQUIRED:
        return _product_required()
    if result.view is SetupView.CHOOSE_LOCATION:
        return _choose_location(result)
    if result.view is SetupView.AWAITING_LOCATION:
        return _awaiting_location(result)
    if result.view is SetupView.LOCATION_RESULTS:
        return _location_results(result)
    if result.view is SetupView.CHOOSE_RADIUS:
        return _choose_radius(result)
    if result.view is SetupView.CHOOSE_SOURCES:
        return _choose_sources(result)
    if result.view is SetupView.CHOOSE_FILTERS:
        return _choose_filters(result)
    if result.view is SetupView.CHOOSE_COMPLETION:
        return _choose_completion(result)
    if result.view is SetupView.AWAITING_END_DATE:
        return _awaiting_end_date(result)
    if result.view is SetupView.REVIEW:
        return _review(result)
    if result.view is SetupView.CREATED:
        return _created(result)
    if result.view is SetupView.CANCELLED:
        return RenderedMessage(
            "Настройка отменена. Подписка и мониторинг не созданы.",
            main_menu_markup(),
        )
    if result.view is SetupView.INPUT_ERROR:
        return _error(result)
    if result.view is SetupView.TEMPORARY_ERROR:
        return _temporary_error(result)
    return _stale()


def _product_required() -> RenderedMessage:
    return RenderedMessage(
        "Сначала выберите и подтвердите точный товар. Без этого подписка не создаётся.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Выбрать товар",
                        callback_data=SubscriptionCallback(action="start").pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Главное меню",
                        callback_data=NavigationCallback(action="main").pack(),
                    )
                ],
            ]
        ),
    )


def _choose_location(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        (
            f"Настройка подписки\n\nТовар: {_product(draft)}\n\n"
            "Выберите географию мониторинга. Города достаточно: точные координаты "
            "передавать необязательно. Адрес с неоднозначным результатом нужно подтвердить."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button("Город", "location_mode", draft, 1),
                    _button("Адрес", "location_mode", draft, 2),
                ],
                [_button("Геопозиция Telegram", "location_mode", draft, 3)],
                [_button("Отменить", "cancel", draft)],
            ]
        ),
    )


def _awaiting_location(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    prompts = {
        LocationInputMode.CITY: "Введите название города.",
        LocationInputMode.ADDRESS: (
            "Введите адрес. Если найдено несколько вариантов, бот попросит выбрать точный."
        ),
        LocationInputMode.COORDINATES: (
            "Отправьте геопозицию через скрепку Telegram → «Геопозиция». "
            "Точные координаты используются только для правила мониторинга."
        ),
    }
    return RenderedMessage(
        prompts[draft.location_mode or LocationInputMode.CITY],
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Другой способ", "edit_location", draft)],
                [_button("Отменить", "cancel", draft)],
            ]
        ),
    )


def _location_results(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    rows: list[list[InlineKeyboardButton]] = []
    lines = ["Найдено несколько вариантов. Выберите подходящий:"]
    for candidate in draft.location_candidates:
        if candidate.ordinal is None:
            continue
        lines.append(f"\n{candidate.ordinal + 1}. {_clip(candidate.display_name, 300)}")
        rows.append(
            [
                _button(
                    f"Выбрать {candidate.ordinal + 1}",
                    "select_location",
                    draft,
                    candidate.ordinal,
                )
            ]
        )
    rows.append([_button("Ввести заново", "edit_location", draft)])
    rows.append([_button("Отменить", "cancel", draft)])
    return RenderedMessage("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _choose_radius(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        (
            f"Локация: {_location(draft)}\n\n"
            "Выберите радиус. Радиус применяется только там, где источник предоставляет "
            "достаточные географические данные."
        ),
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
                [_button("Изменить локацию", "edit_location", draft)],
                [_button("Отменить", "cancel", draft)],
            ]
        ),
    )


def _choose_sources(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    rows: list[list[InlineKeyboardButton]] = []
    lines = [
        f"Локация: {_location(draft)}, радиус {_radius(draft)}",
        "",
        "Выберите источники. Выбор сети не означает, что товар сейчас есть в наличии.",
    ]
    for source in draft.available_sources:
        mark = "✅" if source.code in draft.selected_source_codes else "⬜️"
        if not source.available:
            mark = "⛔️"
            lines.append(
                f"\n{mark} {source.name}: {source.unavailable_reason or 'временно недоступен'}"
            )
        if source.ordinal is not None:
            rows.append([_button(f"{mark} {source.name}", "source", draft, source.ordinal)])
    rows.extend(
        [
            [_button("Продолжить", "sources_done", draft)],
            [
                _button("Изменить локацию", "edit_location", draft),
                _button("Отменить", "cancel", draft),
            ],
        ]
    )
    return RenderedMessage("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _choose_filters(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    selected = [
        item for item in draft.available_sources if item.code in draft.selected_source_codes
    ]
    rows: list[list[InlineKeyboardButton]] = []
    if any(item.supports_low_stock for item in selected):
        rows.append(
            [
                _button(
                    _toggle("Сообщать «мало»", draft.filters.notify_low_stock),
                    "filter",
                    draft,
                    1,
                )
            ]
        )
    if any(item.supports_orderable for item in selected):
        rows.append(
            [
                _button(
                    _toggle(
                        "Учитывать интернет-заказ",
                        draft.filters.notify_orderable,
                    ),
                    "filter",
                    draft,
                    2,
                )
            ]
        )
    if any(item.supports_price for item in selected):
        rows.append(
            [
                _button(
                    _toggle("Показывать цену", draft.filters.include_price),
                    "filter",
                    draft,
                    3,
                )
            ]
        )
    rows.extend(
        [
            [_button("Продолжить", "filters_done", draft)],
            [
                _button("Изменить источники", "edit_sources", draft),
                _button("Отменить", "cancel", draft),
            ],
        ]
    )
    return RenderedMessage(
        (
            "Дополнительные параметры\n\n"
            "Показаны только возможности выбранных источников. "
            "Состояние интернет-заказа не считается подтверждённым остатком в аптеке."
        ),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _choose_completion(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        (
            "Как вести мониторинг после первого успешного уведомления?\n\n"
            "Режим будет виден на итоговом экране и позже может быть изменён."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Продолжать мониторинг", "completion", draft, 1)],
                [_button("Приостановить после успеха", "completion", draft, 2)],
                [_button("Завершить после успеха", "completion", draft, 3)],
                [_button("Работать до даты", "completion", draft, 4)],
                [
                    _button("Изменить фильтры", "edit_filters", draft),
                    _button("Отменить", "cancel", draft),
                ],
            ]
        ),
    )


def _awaiting_end_date(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        (
            "Введите дату окончания в формате ДД.ММ.ГГГГ.\n\n"
            "Дата интерпретируется в Europe/Moscow; бизнес-время сохраняется в UTC."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Другой режим", "edit_completion", draft)],
                [_button("Отменить", "cancel", draft)],
            ]
        ),
    )


def _review(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    sources = ", ".join(
        item.name for item in draft.available_sources if item.code in draft.selected_source_codes
    )
    return RenderedMessage(
        (
            "Проверьте правило мониторинга\n\n"
            f"Товар: {_product(draft)}\n"
            f"Локация: {_location(draft)}\n"
            f"Радиус: {_radius(draft)}\n"
            f"Источники: {sources or 'не выбраны'}\n"
            f"Фильтры: {_filters(draft)}\n"
            f"Режим: {_completion(draft)}\n\n"
            "Подписка создастся только после подтверждения. Текущее наличие, если оно "
            "позже будет получено, показывается как исходное состояние, а не событие появления."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Создать подписку", "confirm", draft)],
                [
                    _button("Локация", "edit", draft, 1),
                    _button("Источники", "edit", draft, 2),
                ],
                [
                    _button("Фильтры", "edit", draft, 3),
                    _button("Режим", "edit", draft, 4),
                ],
                [_button("Отменить", "cancel", draft)],
            ]
        ),
    )


def _created(result: SetupResult) -> RenderedMessage:
    subscription = result.subscription
    draft = _draft(result)
    identifier = subscription.id if subscription else draft.subscription_id
    return RenderedMessage(
        (
            f"Подписка #{identifier} создана\n\n"
            f"Товар: {_product(draft)}\n"
            f"Локация: {_location(draft)}, {_radius(draft)}\n\n"
            "Статус: активна.\n"
            "Текущее состояние: ожидает первой проверки. Это не означает отсутствие товара.\n\n"
            "Мониторинг продолжится по выбранному режиму. Данные источников не являются "
            "гарантией фактического остатка."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Мои подписки",
                        callback_data=NavigationCallback(action="subscriptions").pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Главное меню",
                        callback_data=NavigationCallback(action="main").pack(),
                    )
                ],
            ]
        ),
    )


def _error(result: SetupResult) -> RenderedMessage:
    draft = result.draft
    if draft is None:
        return _stale()
    return RenderedMessage(
        f"Не удалось продолжить.\n\n{result.error}\n\nИсправьте выбор и повторите действие.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Продолжить настройку", _resume_action(draft), draft)],
                [_button("Отменить", "cancel", draft)],
            ]
        ),
    )


def _temporary_error(result: SetupResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        (
            "Сервис уточнения адреса временно недоступен. Это не означает, что локации нет.\n\n"
            "Попробуйте снова, выберите город или отмените настройку."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Выбрать город", "location_mode", draft, 1)],
                [_button("Другой способ", "edit_location", draft)],
                [_button("Отменить", "cancel", draft)],
            ]
        ),
    )


def _stale() -> RenderedMessage:
    return RenderedMessage(
        "Эта кнопка устарела. Откройте актуальный мастер или начните выбор товара заново.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Продолжить настройку",
                        callback_data=SubscriptionCallback(action="configure").pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Главное меню",
                        callback_data=NavigationCallback(action="main").pack(),
                    )
                ],
            ]
        ),
    )


def _button(
    text: str,
    action: str,
    draft: SubscriptionSetupDraft,
    value: int = 0,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=SetupCallback(
            action=action,
            generation=draft.generation,
            value=value,
        ).pack(),
    )


def _draft(result: SetupResult) -> SubscriptionSetupDraft:
    if result.draft is None:
        raise ValueError("setup view requires a draft")
    return result.draft


def _product(draft: SubscriptionSetupDraft) -> str:
    details = [
        value
        for value in (
            draft.product.form,
            draft.product.dosage,
            draft.product.package,
            draft.product.manufacturer,
        )
        if value
    ]
    return _clip(
        f"{draft.product.name}{' · ' + ' · '.join(details) if details else ''}",
        600,
    )


def _location(draft: SubscriptionSetupDraft) -> str:
    return _clip(draft.location.display_name, 500) if draft.location else "не выбрана"


def _radius(draft: SubscriptionSetupDraft) -> str:
    return f"{(draft.radius_meters or 0) / 1000:g} км"


def _toggle(label: str, enabled: bool) -> str:
    return f"{'✅' if enabled else '⬜️'} {label}"


def _filters(draft: SubscriptionSetupDraft) -> str:
    enabled = []
    if draft.filters.notify_low_stock:
        enabled.append("сообщать «мало»")
    if draft.filters.notify_orderable:
        enabled.append("учитывать интернет-заказ")
    if draft.filters.include_price:
        enabled.append("показывать цену")
    return ", ".join(enabled) if enabled else "только подтверждённое наличие"


def _completion(draft: SubscriptionSetupDraft) -> str:
    labels = {
        CompletionMode.CONTINUE: "продолжать мониторинг",
        CompletionMode.PAUSE_AFTER_SUCCESS: "приостановить после успеха",
        CompletionMode.COMPLETE_AFTER_SUCCESS: "завершить после успеха",
        CompletionMode.UNTIL_DATE: "работать до даты",
    }
    text = labels[draft.completion_mode] if draft.completion_mode is not None else "не выбран"
    if draft.completion_mode is CompletionMode.UNTIL_DATE and draft.ends_at:
        text += f" ({draft.ends_at.astimezone(UTC):%d.%m.%Y} UTC)"
    return text


def _resume_action(draft: SubscriptionSetupDraft) -> str:
    return {
        SetupStatus.AWAITING_LOCATION: "edit_location",
        SetupStatus.CONFIRM_LOCATION: "edit_location",
        SetupStatus.CHOOSE_RADIUS: "edit_location",
        SetupStatus.CHOOSE_SOURCES: "edit_sources",
        SetupStatus.CHOOSE_FILTERS: "edit_filters",
        SetupStatus.CHOOSE_COMPLETION: "edit_completion",
        SetupStatus.AWAITING_END_DATE: "edit_completion",
        SetupStatus.REVIEW: "edit_completion",
    }.get(draft.status, "edit_location")


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1]}…"
