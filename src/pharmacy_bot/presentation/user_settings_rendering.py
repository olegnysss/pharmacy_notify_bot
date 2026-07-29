from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pharmacy_bot.application.user_settings import SettingsResult, SettingsView
from pharmacy_bot.domain.subscription_setup import CompletionMode, LocationInputMode
from pharmacy_bot.domain.user_settings import UserPreferences
from pharmacy_bot.presentation.callbacks import NavigationCallback, SettingsCallback
from pharmacy_bot.presentation.rendering import RenderedMessage, render_onboarding


def render_user_settings(result: SettingsResult) -> RenderedMessage:
    if result.view is SettingsView.ONBOARDING:
        return render_onboarding(result.onboarding)
    if result.preferences is None:
        return _simple("Настройки недоступны. Откройте /settings ещё раз.")
    if result.view is SettingsView.DASHBOARD:
        return _dashboard(result)
    if result.view is SettingsView.CHOOSE_LOCATION:
        return _location(result)
    if result.view is SettingsView.AWAITING_LOCATION:
        return _awaiting_location(result)
    if result.view is SettingsView.LOCATION_RESULTS:
        return _location_results(result)
    if result.view is SettingsView.CHOOSE_RADIUS:
        return _radius(result)
    if result.view is SettingsView.CHOOSE_SOURCES:
        return _sources(result)
    if result.view is SettingsView.LANGUAGE:
        return _language(result)
    if result.view is SettingsView.TIMEZONE:
        return _timezone(result)
    if result.view is SettingsView.NOTIFICATIONS:
        return _notifications(result)
    if result.view is SettingsView.LIMITS:
        return _limits(result)
    if result.view is SettingsView.SAVED:
        return RenderedMessage(
            (
                "Настройки сохранены. Они применяются к новым подпискам и не переписывают "
                "уже созданные правила."
            ),
            InlineKeyboardMarkup(
                inline_keyboard=[[_button("Все настройки", "section", result.preferences, 6)]]
            ),
        )
    if result.view in {SettingsView.INPUT_ERROR, SettingsView.TEMPORARY_ERROR}:
        return RenderedMessage(
            f"Не удалось сохранить: {result.error or 'временная ошибка'}.",
            InlineKeyboardMarkup(
                inline_keyboard=[[_button("Вернуться", "section", result.preferences, 6)]]
            ),
        )
    return RenderedMessage(
        "Экран настроек устарел. Откройте актуальные значения.",
        InlineKeyboardMarkup(
            inline_keyboard=[[_navigation_button("Открыть /settings", "settings")]]
        ),
    )


def _dashboard(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value
    usage = result.usage
    defaults = (
        f"{value.default_location.display_name}, {value.default_radius_meters / 1000:g} км"
        if value.default_location and value.default_radius_meters
        else "не заданы"
    )
    return RenderedMessage(
        (
            "Настройки\n\n"
            f"Локация и сети по умолчанию: {defaults}\n"
            f"Язык: Русский\n"
            f"Часовой пояс: {value.timezone_name}\n"
            f"Тихие часы: {_on(value.quiet_hours_enabled)} "
            f"({value.quiet_hours_start:%H:%M}–{value.quiet_hours_end:%H:%M})\n"
            f"Сводка после тихих часов: {_on(value.digest_enabled)}\n"
            f"Активные подписки: "
            f"{usage.active_subscriptions if usage else '?'} / "
            f"{usage.max_active_subscriptions if usage else '?'}\n\n"
            "Defaults всегда показываются в итоговом экране новой подписки и могут быть "
            "переопределены. Существующие подписки не меняются."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Локация, радиус и сети", "section", value, 1)],
                [
                    _button("Язык", "section", value, 2),
                    _button("Часовой пояс", "section", value, 3),
                ],
                [_button("Уведомления", "section", value, 4)],
                [_button("Лимиты сервиса", "section", value, 5)],
                [_navigation_button("Главное меню", "main")],
            ]
        ),
    )


def _location(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value
    current = (
        f"{value.default_location.display_name}, {value.default_radius_meters / 1000:g} км; "
        f"источники: {', '.join(value.default_source_codes) or 'нет'}"
        if value.default_location and value.default_radius_meters
        else "не заданы"
    )
    return RenderedMessage(
        (
            f"Defaults области мониторинга\n\nТекущие: {current}\n\n"
            "Города достаточно — точные координаты необязательны. Неоднозначный адрес "
            "потребует подтверждения."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button("Город", "location_mode", value, 1),
                    _button("Адрес", "location_mode", value, 2),
                ],
                [_button("Геопозиция Telegram", "location_mode", value, 3)],
                [_button("Очистить defaults", "clear", value)],
                [_button("Назад", "section", value, 6)],
            ]
        ),
    )


def _awaiting_location(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value
    prompts = {
        LocationInputMode.CITY: "Введите город.",
        LocationInputMode.ADDRESS: "Введите адрес.",
        LocationInputMode.COORDINATES: "Отправьте геопозицию через скрепку Telegram.",
    }
    return RenderedMessage(
        prompts[value.location_mode or LocationInputMode.CITY],
        InlineKeyboardMarkup(inline_keyboard=[[_button("Другой способ", "section", value, 1)]]),
    )


def _location_results(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value
    rows: list[list[InlineKeyboardButton]] = []
    lines = ["Подтвердите найденную локацию:"]
    for candidate in value.location_candidates:
        if candidate.ordinal is None:
            continue
        lines.append(f"\n{candidate.ordinal + 1}. {candidate.display_name}")
        rows.append(
            [
                _button(
                    f"Выбрать {candidate.ordinal + 1}", "select_location", value, candidate.ordinal
                )
            ]
        )
    rows.append([_button("Ввести заново", "section", value, 1)])
    return RenderedMessage("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _radius(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value and value.default_location
    return RenderedMessage(
        f"Локация: {value.default_location.display_name}\n\nВыберите допустимый радиус.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button("1 км", "radius", value, 1000),
                    _button("3 км", "radius", value, 3000),
                    _button("5 км", "radius", value, 5000),
                ],
                [
                    _button("10 км", "radius", value, 10000),
                    _button("25 км", "radius", value, 25000),
                ],
                [_button("Назад", "section", value, 1)],
            ]
        ),
    )


def _sources(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value
    rows: list[list[InlineKeyboardButton]] = []
    lines = ["Выберите сети по умолчанию. Недоступные сети не сохраняются активными."]
    for source in result.sources:
        mark = "✅" if source.code in value.default_source_codes else "⬜️"
        if not source.available:
            mark = "⛔️"
            lines.append(f"\n{source.name}: {source.unavailable_reason or 'недоступен'}")
        if source.ordinal is not None:
            rows.append([_button(f"{mark} {source.name}", "source", value, source.ordinal)])
    rows.append([_button("Сохранить defaults", "sources_done", value)])
    rows.append([_button("Назад", "section", value, 1)])
    return RenderedMessage("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _language(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value
    return RenderedMessage(
        (
            "Язык интерфейса\n\n✅ Русский\n\n"
            "Сейчас полностью поддерживается русский. Неподдерживаемая локаль не будет "
            "выдана за активную; расширение каталога переводов выполняется отдельно."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button("Русский", "language", value, 1)],
                [_button("Назад", "section", value, 6)],
            ]
        ),
    )


def _timezone(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value
    zones = ("Europe/Moscow", "Europe/Kaliningrad", "Asia/Yekaterinburg", "UTC")
    return RenderedMessage(
        (
            f"Часовой пояс: {value.timezone_name}\n\n"
            "Он влияет на отображение дат и будущие тихие часы. Сохранённые абсолютные "
            "моменты подписок остаются в UTC и не сдвигаются."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _button(
                        ("✅ " if zone == value.timezone_name else "") + zone, "timezone", value, i
                    )
                ]
                for i, zone in enumerate(zones)
            ]
            + [[_button("Назад", "section", value, 6)]],
        ),
    )


def _notifications(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    assert value
    completion = {
        CompletionMode.CONTINUE: "продолжать",
        CompletionMode.PAUSE_AFTER_SUCCESS: "пауза после успеха",
        CompletionMode.COMPLETE_AFTER_SUCCESS: "завершить после успеха",
        CompletionMode.UNTIL_DATE: "до даты (задаётся в подписке)",
    }[value.completion_mode]
    return RenderedMessage(
        (
            "Глобальные предпочтения новых подписок\n\n"
            f"Low stock: {_on(value.filters.notify_low_stock)}\n"
            f"Интернет-заказ: {_on(value.filters.notify_orderable)}\n"
            f"Показывать цену: {_on(value.filters.include_price)}\n"
            f"Тихие часы 22:00–08:00 ({value.timezone_name}): "
            f"{_on(value.quiet_hours_enabled)}\n"
            f"Сводка после них: {_on(value.digest_enabled)}\n"
            f"Точек в сообщении: {value.max_points_per_message}\n"
            f"Режим завершения: {completion}\n\n"
            "Опции источников применятся только там, где они реально поддерживаются. "
            "Явные настройки конкретной подписки имеют приоритет."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_button(_toggle("Low stock", value.filters.notify_low_stock), "notify", value, 1)],
                [
                    _button(
                        _toggle("Интернет-заказ", value.filters.notify_orderable),
                        "notify",
                        value,
                        2,
                    )
                ],
                [_button(_toggle("Цена", value.filters.include_price), "notify", value, 3)],
                [
                    _button(_toggle("Тихие часы", value.quiet_hours_enabled), "notify", value, 4),
                    _button(_toggle("Сводка", value.digest_enabled), "notify", value, 5),
                ],
                [
                    _button("3 точки", "notify", value, 6),
                    _button("5 точек", "notify", value, 7),
                    _button("10 точек", "notify", value, 8),
                ],
                [
                    _button("Продолжать", "notify", value, 9),
                    _button("Пауза", "notify", value, 10),
                ],
                [_button("Завершать", "notify", value, 11)],
                [_button("Назад", "section", value, 6)],
            ]
        ),
    )


def _limits(result: SettingsResult) -> RenderedMessage:
    value = result.preferences
    limits = result.limits
    usage = result.usage
    assert value and limits
    return RenderedMessage(
        (
            "Лимиты сервиса\n\n"
            f"Активные подписки: {usage.active_subscriptions if usage else '?'} / "
            f"{limits.max_active_subscriptions}\n"
            f"Радиус: {limits.min_radius_meters / 1000:g}–"
            f"{limits.max_radius_meters / 1000:g} км\n"
            f"Источников в подписке: до {limits.max_sources_per_subscription}\n"
            f"Повтор ручной проверки: через {limits.manual_check_cooldown_seconds} сек.\n"
            f"Длина запроса товара: {limits.product_query_min_length}–"
            f"{limits.product_query_max_length} символов\n\n"
            "При достижении лимита приостановите или удалите ненужную подписку; введённые "
            "параметры не должны теряться."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_navigation_button("Управлять подписками", "subscriptions")],
                [_button("Назад", "section", value, 6)],
            ]
        ),
    )


def _button(
    text: str,
    action: str,
    preferences: UserPreferences,
    value: int = 0,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=SettingsCallback(
            action=action,
            generation=preferences.generation,
            value=value,
        ).pack(),
    )


def _navigation_button(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=NavigationCallback(action=action).pack(),
    )


def _simple(text: str) -> RenderedMessage:
    return RenderedMessage(
        text,
        InlineKeyboardMarkup(inline_keyboard=[[_navigation_button("Главное меню", "main")]]),
    )


def _on(value: bool) -> str:
    return "включено" if value else "выключено"


def _toggle(text: str, value: bool) -> str:
    return f"{'✅' if value else '⬜️'} {text}"
