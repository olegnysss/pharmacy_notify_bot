from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pharmacy_bot.application.product_selection import (
    ProductSelectionResult,
    ProductSelectionView,
)
from pharmacy_bot.domain.product_selection import (
    MatchConfidence,
    ProductCandidate,
    ProductDraft,
    ProductInputMode,
)
from pharmacy_bot.presentation.callbacks import (
    NavigationCallback,
    ProductCallback,
    SubscriptionCallback,
)
from pharmacy_bot.presentation.rendering import (
    RenderedMessage,
    main_menu_markup,
    render_onboarding,
)


def render_product_selection(result: ProductSelectionResult) -> RenderedMessage:
    if result.view is ProductSelectionView.ONBOARDING:
        return render_onboarding(result.onboarding)
    if result.view is ProductSelectionView.CHOOSE_METHOD:
        return _render_method(result)
    if result.view is ProductSelectionView.AWAITING_INPUT:
        return _render_input(result)
    if result.view is ProductSelectionView.INPUT_ERROR:
        return _render_input_error(result)
    if result.view is ProductSelectionView.RESULTS:
        return _render_results(result)
    if result.view is ProductSelectionView.NO_RESULTS:
        return _render_no_results(result)
    if result.view is ProductSelectionView.TEMPORARY_ERROR:
        return _render_temporary_error(result)
    if result.view is ProductSelectionView.CONFIRMATION:
        return _render_confirmation(result)
    if result.view is ProductSelectionView.CONFIRMED:
        return _render_confirmed(result)
    if result.view is ProductSelectionView.CANCELLED:
        return _render_cancelled()
    return _render_stale(result)


def _render_method(result: ProductSelectionResult) -> RenderedMessage:
    generation = _generation(result)
    return RenderedMessage(
        text=(
            "Добавление товара\n\n"
            "Выберите поиск по названию или отправку HTTPS-ссылки поддерживаемой аптечной "
            "сети. Бот не открывает произвольные URL.\n\n"
            "Перед выбором обязательно проверьте форму, дозировку, упаковку и производителя."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Поиск по названию",
                        callback_data=_callback("mode_search", generation),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Ссылка на товар",
                        callback_data=_callback("mode_link", generation),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data=NavigationCallback(action="main").pack(),
                    ),
                    InlineKeyboardButton(
                        text="Отменить",
                        callback_data=_callback("cancel", generation),
                    ),
                ],
            ]
        ),
    )


def _render_input(result: ProductSelectionResult) -> RenderedMessage:
    draft = _draft(result)
    if draft.input_mode is ProductInputMode.LINK:
        prompt = (
            "Отправьте HTTPS-ссылку на карточку товара поддерживаемой аптечной сети.\n\n"
            "Домен проверяется по allowlist. Ссылка не будет загружена напрямую Telegram-слоем."
        )
    else:
        prompt = (
            "Отправьте название товара. Укажите форму, дозировку и упаковку, "
            "если они вам известны — это уменьшит неоднозначность."
        )
    return RenderedMessage(
        text=prompt,
        reply_markup=_input_actions(draft.generation),
    )


def _render_input_error(result: ProductSelectionResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        text=f"Не удалось обработать ввод.\n\n{result.error}\n\nИсправьте ввод и отправьте снова.",
        reply_markup=_input_actions(draft.generation),
    )


def _render_results(result: ProductSelectionResult) -> RenderedMessage:
    draft = _draft(result)
    lines = [
        f"Результаты для: {draft.query_text}",
        f"Страница {result.page + 1} из {result.total_pages}",
        "",
        "Выберите точную позицию:",
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for candidate in result.candidates:
        lines.extend(["", _candidate_text(candidate, compact=True)])
        ordinal = candidate.ordinal
        if ordinal is not None:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Выбрать {ordinal + 1}",
                        callback_data=_callback("select", draft.generation, ordinal),
                    )
                ]
            )

    pagination: list[InlineKeyboardButton] = []
    if result.page > 0:
        pagination.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=_callback("page", draft.generation, result.page - 1),
            )
        )
    if result.page + 1 < result.total_pages:
        pagination.append(
            InlineKeyboardButton(
                text="Вперёд →",
                callback_data=_callback("page", draft.generation, result.page + 1),
            )
        )
    if pagination:
        rows.append(pagination)
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Новый поиск",
                    callback_data=_callback("mode_search", draft.generation),
                ),
                InlineKeyboardButton(
                    text="Другой способ",
                    callback_data=_callback("methods", draft.generation),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=_callback("cancel", draft.generation),
                )
            ],
        ]
    )
    return RenderedMessage(
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _render_no_results(result: ProductSelectionResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        text=(
            f"По запросу «{draft.query_text}» точные кандидаты не найдены.\n\n"
            "Широкая подписка не создана. Уточните форму, дозировку или упаковку либо "
            "попробуйте ссылку поддерживаемого источника."
        ),
        reply_markup=_retry_actions(draft.generation),
    )


def _render_temporary_error(result: ProductSelectionResult) -> RenderedMessage:
    draft = _draft(result)
    return RenderedMessage(
        text=(
            "Каталог или источник временно недоступен. Подписка не создана.\n\n"
            "Можно повторить ввод, выбрать другой способ или вернуться позже."
        ),
        reply_markup=_retry_actions(draft.generation),
    )


def _render_confirmation(result: ProductSelectionResult) -> RenderedMessage:
    draft = _draft(result)
    candidate = draft.selected_candidate
    if candidate is None or candidate.ordinal is None:
        return _render_stale(result)

    missing = [
        label
        for label, value in (
            ("форма", candidate.form),
            ("дозировка", candidate.dosage),
            ("упаковка", candidate.package),
            ("производитель", candidate.manufacturer),
        )
        if not value
    ]
    warning = _confidence_warning(candidate.confidence)
    if missing:
        warning = f"{warning}\nНе указано: {', '.join(missing)}."
    return RenderedMessage(
        text=(
            "Проверьте точный товар\n\n"
            f"{_candidate_text(candidate, compact=False)}\n\n"
            f"{warning}\n\n"
            "Подтверждение сохраняет выбранный идентификатор и версию в черновике, "
            "но ещё не создаёт подписку."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Подтверждаю этот товар",
                        callback_data=_callback(
                            "confirm",
                            draft.generation,
                            candidate.ordinal,
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="К результатам",
                        callback_data=_callback("results", draft.generation),
                    ),
                    InlineKeyboardButton(
                        text="Новый поиск",
                        callback_data=_callback("mode_search", draft.generation),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Отменить",
                        callback_data=_callback("cancel", draft.generation),
                    )
                ],
            ]
        ),
    )


def _render_confirmed(result: ProductSelectionResult) -> RenderedMessage:
    draft = _draft(result)
    candidate = draft.selected_candidate
    if candidate is None:
        return _render_stale(result)
    return RenderedMessage(
        text=(
            "Товар подтверждён\n\n"
            f"{_candidate_text(candidate, compact=False)}\n\n"
            "Выбор сохранён в черновике. Подписка ещё не создана: область мониторинга "
            "и итоговое подтверждение относятся к следующей story."
        ),
        reply_markup=InlineKeyboardMarkup(
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


def _render_cancelled() -> RenderedMessage:
    return RenderedMessage(
        text=(
            "Выбор товара отменён. Черновые результаты удалены, подписка и мониторинг не созданы."
        ),
        reply_markup=main_menu_markup(),
    )


def _render_stale(result: ProductSelectionResult) -> RenderedMessage:
    restart_callback = (
        _callback("mode_search", result.draft.generation)
        if result.draft is not None
        else SubscriptionCallback(action="start").pack()
    )
    return RenderedMessage(
        text=(
            "Эта кнопка относится к устаревшей или изменившейся выдаче. "
            "Начните новый поиск — старый выбор не применён."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Поиск по названию",
                        callback_data=restart_callback,
                    ),
                    InlineKeyboardButton(
                        text="Главное меню",
                        callback_data=NavigationCallback(action="main").pack(),
                    ),
                ]
            ]
        ),
    )


def _input_actions(generation: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Другой способ",
                    callback_data=_callback("methods", generation),
                ),
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=_callback("cancel", generation),
                ),
            ]
        ]
    )


def _retry_actions(generation: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Искать по названию",
                    callback_data=_callback("mode_search", generation),
                ),
                InlineKeyboardButton(
                    text="Отправить ссылку",
                    callback_data=_callback("mode_link", generation),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=_callback("cancel", generation),
                )
            ],
        ]
    )


def _candidate_text(candidate: ProductCandidate, *, compact: bool) -> str:
    ordinal = f"{candidate.ordinal + 1}. " if candidate.ordinal is not None else ""
    details = [
        _clip(value, 80)
        for value in (
            candidate.form,
            candidate.dosage,
            candidate.package,
            candidate.manufacturer,
        )
        if value
    ]
    text = f"{ordinal}{_clip(candidate.name, 160)}"
    if details:
        text += f"\n   {' · '.join(details)}"
    if candidate.source_name:
        text += f"\n   Источник: {_clip(candidate.source_name, 80)}"
    if not compact:
        text += f"\nУверенность: {_confidence_label(candidate.confidence)}"
        if candidate.source_host:
            text += f"\nДомен источника: {_clip(candidate.source_host, 255)}"
    return text


def _clip(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1]}…"


def _confidence_label(confidence: MatchConfidence) -> str:
    return {
        MatchConfidence.EXACT: "точное соответствие",
        MatchConfidence.PROBABLE: "вероятное соответствие",
        MatchConfidence.CANDIDATE: "требуется особое внимание",
    }[confidence]


def _confidence_warning(confidence: MatchConfidence) -> str:
    if confidence is MatchConfidence.EXACT:
        return "Характеристики отмечены как точное соответствие."
    if confidence is MatchConfidence.PROBABLE:
        return (
            "Это вероятное соответствие без надёжного общего идентификатора. "
            "Подтвердите только после проверки характеристик."
        )
    return (
        "Кандидат неоднозначен. Бот не выберет другую дозировку, форму или упаковку автоматически."
    )


def _callback(action: str, generation: int, value: int = 0) -> str:
    return ProductCallback(
        action=action,
        generation=generation,
        value=value,
    ).pack()


def _generation(result: ProductSelectionResult) -> int:
    return _draft(result).generation


def _draft(result: ProductSelectionResult) -> ProductDraft:
    if result.draft is None:
        raise RuntimeError("product selection view requires a draft")
    return result.draft
