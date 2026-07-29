from __future__ import annotations

from datetime import UTC, datetime

from pharmacy_bot.application.onboarding import (
    DocumentBundle,
    OnboardingResult,
    OnboardingView,
)
from pharmacy_bot.application.product_selection import (
    ProductSelectionResult,
    ProductSelectionView,
)
from pharmacy_bot.domain.onboarding import (
    OnboardingStatus,
    TelegramIdentity,
    UserSnapshot,
)
from pharmacy_bot.domain.product_selection import (
    MatchConfidence,
    ProductCandidate,
    ProductDraft,
    ProductDraftStatus,
    ProductInputMode,
)
from pharmacy_bot.presentation.product_selection_rendering import (
    render_product_selection,
)
from pharmacy_bot.presentation.rendering import RenderedMessage


def onboarding() -> OnboardingResult:
    return OnboardingResult(
        view=OnboardingView.MAIN_MENU,
        user=UserSnapshot(
            id=1,
            identity=TelegramIdentity(telegram_user_id=10, telegram_chat_id=10),
            status=OnboardingStatus.COMPLETED,
        ),
        documents=DocumentBundle(
            terms_version="terms-v1",
            terms_url="https://example.com/terms",
            privacy_version="privacy-v1",
            privacy_url="https://example.com/privacy",
        ),
    )


def candidate(
    ordinal: int,
    *,
    confidence: MatchConfidence = MatchConfidence.CANDIDATE,
    with_details: bool = True,
) -> ProductCandidate:
    return ProductCandidate(
        candidate_key=f"candidate-{ordinal}",
        version=f"v{ordinal}",
        name=f"Товар {ordinal}",
        form="таблетки" if with_details else None,
        dosage="10 мг" if with_details else None,
        package="№20" if with_details else None,
        manufacturer="Производитель" if with_details else None,
        source_name="Аптека",
        source_host="shop.example",
        confidence=confidence,
        ordinal=ordinal,
    )


def draft(
    status: ProductDraftStatus,
    *,
    candidates: tuple[ProductCandidate, ...] = (),
    selected_ordinal: int | None = None,
) -> ProductDraft:
    selected = next(
        (item for item in candidates if item.ordinal == selected_ordinal),
        None,
    )
    return ProductDraft(
        id=1,
        user_id=1,
        generation=7,
        status=status,
        input_mode=ProductInputMode.SEARCH,
        query_text="товар 10 мг",
        source_host=None,
        candidates=candidates,
        selected_ordinal=selected_ordinal,
        selected_candidate_version=selected.version if selected else None,
        expires_at=datetime(2026, 7, 29, 13, 0, tzinfo=UTC),
    )


def callback_values(rendered: RenderedMessage) -> set[str]:
    return {
        button.callback_data
        for row in rendered.reply_markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    }


def test_initial_screen_explains_exactness_and_does_not_promise_any_url() -> None:
    rendered = render_product_selection(
        ProductSelectionResult(
            ProductSelectionView.CHOOSE_METHOD,
            onboarding(),
            draft(ProductDraftStatus.CHOOSE_METHOD),
        )
    )

    assert "не открывает произвольные URL" in rendered.text
    assert "форму, дозировку, упаковку" in rendered.text
    assert callback_values(rendered) >= {
        "product:mode_search:7:0",
        "product:mode_link:7:0",
        "product:cancel:7:0",
    }


def test_results_show_critical_characteristics_and_stable_pagination() -> None:
    page_candidates = (candidate(3), candidate(4))
    rendered = render_product_selection(
        ProductSelectionResult(
            ProductSelectionView.RESULTS,
            onboarding(),
            draft(ProductDraftStatus.RESULTS, candidates=page_candidates),
            candidates=page_candidates,
            page=1,
            total_pages=3,
        )
    )

    assert "таблетки · 10 мг · №20 · Производитель" in rendered.text
    assert "Страница 2 из 3" in rendered.text
    callbacks = callback_values(rendered)
    assert "product:page:7:0" in callbacks
    assert "product:page:7:2" in callbacks
    assert "product:select:7:3" in callbacks
    assert all("Товар" not in value and "мг" not in value for value in callbacks)


def test_ambiguous_candidate_and_missing_fields_are_explicit_before_confirmation() -> None:
    selected = candidate(0, with_details=False)
    rendered = render_product_selection(
        ProductSelectionResult(
            ProductSelectionView.CONFIRMATION,
            onboarding(),
            draft(
                ProductDraftStatus.CONFIRMATION,
                candidates=(selected,),
                selected_ordinal=0,
            ),
        )
    )

    assert "Кандидат неоднозначен" in rendered.text
    assert "Не указано: форма, дозировка, упаковка, производитель" in rendered.text
    assert "ещё не создаёт подписку" in rendered.text
    assert "product:confirm:7:0" in callback_values(rendered)


def test_probable_match_requires_explicit_user_check() -> None:
    selected = candidate(0, confidence=MatchConfidence.PROBABLE)
    rendered = render_product_selection(
        ProductSelectionResult(
            ProductSelectionView.CONFIRMATION,
            onboarding(),
            draft(
                ProductDraftStatus.CONFIRMATION,
                candidates=(selected,),
                selected_ordinal=0,
            ),
        )
    )

    assert "вероятное соответствие" in rendered.text
    assert "Подтвердите только после проверки" in rendered.text


def test_confirmed_product_is_only_a_draft_handoff_to_next_story() -> None:
    selected = candidate(0, confidence=MatchConfidence.EXACT)
    rendered = render_product_selection(
        ProductSelectionResult(
            ProductSelectionView.CONFIRMED,
            onboarding(),
            draft(
                ProductDraftStatus.CONFIRMED,
                candidates=(selected,),
                selected_ordinal=0,
            ),
        )
    )

    assert "Подписка ещё не создана" in rendered.text
    assert "subscription:configure" in callback_values(rendered)


def test_stale_page_cannot_reuse_candidate_callback() -> None:
    rendered = render_product_selection(
        ProductSelectionResult(
            ProductSelectionView.STALE,
            onboarding(),
            draft(ProductDraftStatus.RESULTS),
        )
    )

    assert "устаревшей или изменившейся выдаче" in rendered.text
    assert not any(value.startswith("product:select") for value in callback_values(rendered))
