from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from pharmacy_bot.application.onboarding import DocumentBundle, OnboardingService
from pharmacy_bot.application.product_selection import (
    ProductSelectionService,
    ProductSelectionView,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.product_selection import (
    DiscoveryResponse,
    DiscoveryStatus,
    MatchConfidence,
    ProductCandidate,
    ProductInputMode,
)
from pharmacy_bot.infrastructure.product_discovery import ConfiguredProductLinkPolicy
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


@pytest.fixture
def identity() -> TelegramIdentity:
    return TelegramIdentity(telegram_user_id=1001, telegram_chat_id=1001)


@pytest.fixture
def onboarding_service() -> OnboardingService:
    return OnboardingService(
        InMemoryOnboardingRepository(),
        DocumentBundle(
            terms_version="terms-v1",
            terms_url="https://example.com/terms",
            privacy_version="privacy-v1",
            privacy_url="https://example.com/privacy",
        ),
    )


@pytest.fixture
def repository() -> InMemoryProductDraftRepository:
    return InMemoryProductDraftRepository()


@pytest.fixture
def candidates() -> tuple[ProductCandidate, ...]:
    return tuple(
        ProductCandidate(
            candidate_key=f"product-{index}",
            version=f"v{index}",
            name=f"Товар {index}",
            form="таблетки",
            dosage=f"{index + 1} мг",
            package=f"№{10 + index}",
            manufacturer=f"Производитель {index}",
            source_name="Тестовая аптека",
            source_host="shop.example",
            confidence=(MatchConfidence.EXACT if index == 0 else MatchConfidence.CANDIDATE),
        )
        for index in range(7)
    )


def build_service(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    discovery: FakeProductDiscoveryGateway,
) -> ProductSelectionService:
    return ProductSelectionService(
        onboarding_service,
        repository,
        discovery,
        ConfiguredProductLinkPolicy(("shop.example",)),
        query_min_length=2,
        query_max_length=40,
        url_max_length=200,
        page_size=3,
        draft_ttl=timedelta(hours=1),
        clock=FixedClock(datetime(2026, 7, 29, 12, 0, tzinfo=UTC)),
    )


async def accept(onboarding: OnboardingService, identity: TelegramIdentity) -> None:
    await onboarding.accept(identity)


async def test_start_cannot_bypass_onboarding_and_does_not_create_draft(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
    candidates: tuple[ProductCandidate, ...],
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.SUCCESS, candidates)
    )
    service = build_service(onboarding_service, repository, discovery)

    result = await service.start(identity)

    assert result.view is ProductSelectionView.ONBOARDING
    assert not repository.drafts


async def test_repeated_entrypoints_restore_one_active_draft(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.EMPTY)
    )
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)

    first = await service.start(identity)
    second = await service.start(identity)

    assert first.view is ProductSelectionView.CHOOSE_METHOD
    assert second.draft == first.draft
    assert len(repository.drafts) == 1
    assert repository.subscription_count == 0


async def test_search_normalizes_spaces_and_returns_only_current_candidates(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
    candidates: tuple[ProductCandidate, ...],
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.SUCCESS, candidates)
    )
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)
    started = await service.start(identity)
    awaiting = await service.choose_input(
        identity,
        ProductInputMode.SEARCH,
        generation=started.draft.generation,  # type: ignore[union-attr]
    )

    result = await service.submit_text(identity, "  Товар\u00a0  10 мг  ")

    assert awaiting.view is ProductSelectionView.AWAITING_INPUT
    assert result is not None
    assert result.view is ProductSelectionView.RESULTS
    assert discovery.search_calls == ["Товар 10 мг"]
    assert [item.ordinal for item in result.candidates] == [0, 1, 2]
    assert result.total_pages == 3


@pytest.mark.parametrize("query", [" ", "-", "a", "x" * 41])
async def test_invalid_search_explains_correction_without_calling_catalog(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
    query: str,
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.EMPTY)
    )
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)
    started = await service.start(identity)
    await service.choose_input(
        identity,
        ProductInputMode.SEARCH,
        generation=started.draft.generation,  # type: ignore[union-attr]
    )

    result = await service.submit_text(identity, query)

    assert result is not None
    assert result.view is ProductSelectionView.INPUT_ERROR
    assert result.error
    assert not discovery.search_calls


async def test_unsupported_url_is_never_sent_to_discovery_gateway(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.EMPTY)
    )
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)
    started = await service.start(identity)
    await service.choose_input(
        identity,
        ProductInputMode.LINK,
        generation=started.draft.generation,  # type: ignore[union-attr]
    )

    result = await service.submit_text(identity, "https://127.0.0.1/product/1")

    assert result is not None
    assert result.view is ProductSelectionView.INPUT_ERROR
    assert not discovery.link_calls


async def test_supported_link_keeps_source_and_requires_selection(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
    candidates: tuple[ProductCandidate, ...],
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.EMPTY),
        link_response=DiscoveryResponse(DiscoveryStatus.SUCCESS, (candidates[0],)),
    )
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)
    started = await service.start(identity)
    await service.choose_input(
        identity,
        ProductInputMode.LINK,
        generation=started.draft.generation,  # type: ignore[union-attr]
    )

    result = await service.submit_text(identity, "https://shop.example/product/42")

    assert result is not None
    assert result.view is ProductSelectionView.RESULTS
    assert discovery.link_calls == [("shop.example", "https://shop.example/product/42")]
    assert result.draft is not None
    assert result.draft.source_host == "shop.example"
    assert repository.subscription_count == 0


async def test_pagination_is_bound_to_generation_and_rejects_old_page(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
    candidates: tuple[ProductCandidate, ...],
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.SUCCESS, candidates)
    )
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)
    started = await service.start(identity)
    await service.choose_input(
        identity,
        ProductInputMode.SEARCH,
        generation=started.draft.generation,  # type: ignore[union-attr]
    )
    first = await service.submit_text(identity, "первый запрос")
    assert first is not None and first.draft is not None
    old_generation = first.draft.generation

    second = await service.submit_text(identity, "второй запрос")
    assert second is not None and second.draft is not None
    current_page = await service.show_page(
        identity,
        generation=second.draft.generation,
        page=2,
    )
    stale_page = await service.show_page(
        identity,
        generation=old_generation,
        page=1,
    )

    assert current_page.view is ProductSelectionView.RESULTS
    assert [item.ordinal for item in current_page.candidates] == [6]
    assert stale_page.view is ProductSelectionView.STALE


async def test_candidate_requires_explicit_selection_and_versioned_confirmation(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
    candidates: tuple[ProductCandidate, ...],
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.SUCCESS, candidates)
    )
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)
    started = await service.start(identity)
    await service.choose_input(
        identity,
        ProductInputMode.SEARCH,
        generation=started.draft.generation,  # type: ignore[union-attr]
    )
    results = await service.submit_text(identity, "товар")
    assert results is not None and results.draft is not None

    selected = await service.select_candidate(
        identity,
        generation=results.draft.generation,
        ordinal=1,
    )
    confirmed = await service.confirm_candidate(
        identity,
        generation=results.draft.generation,
        ordinal=1,
    )

    assert selected.view is ProductSelectionView.CONFIRMATION
    assert selected.draft is not None
    assert selected.draft.selected_candidate_version == "v1"
    assert confirmed.view is ProductSelectionView.CONFIRMED
    assert repository.subscription_count == 0


async def test_cancel_invalidates_callbacks_and_creates_no_subscription(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
) -> None:
    discovery = FakeProductDiscoveryGateway(
        search_response=DiscoveryResponse(DiscoveryStatus.EMPTY)
    )
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)
    started = await service.start(identity)
    assert started.draft is not None
    old_generation = started.draft.generation

    cancelled = await service.cancel(identity, generation=old_generation)
    stale = await service.choose_input(
        identity,
        ProductInputMode.SEARCH,
        generation=old_generation,
    )

    assert cancelled.view is ProductSelectionView.CANCELLED
    assert stale.view is ProductSelectionView.STALE
    assert repository.subscription_count == 0


@pytest.mark.parametrize(
    ("status", "view"),
    [
        (DiscoveryStatus.EMPTY, ProductSelectionView.NO_RESULTS),
        (DiscoveryStatus.TEMPORARY_ERROR, ProductSelectionView.TEMPORARY_ERROR),
    ],
)
async def test_empty_and_temporary_error_never_create_wide_subscription(
    onboarding_service: OnboardingService,
    repository: InMemoryProductDraftRepository,
    identity: TelegramIdentity,
    status: DiscoveryStatus,
    view: ProductSelectionView,
) -> None:
    discovery = FakeProductDiscoveryGateway(search_response=DiscoveryResponse(status))
    service = build_service(onboarding_service, repository, discovery)
    await accept(onboarding_service, identity)
    started = await service.start(identity)
    await service.choose_input(
        identity,
        ProductInputMode.SEARCH,
        generation=started.draft.generation,  # type: ignore[union-attr]
    )

    result = await service.submit_text(identity, "редкий товар")

    assert result is not None
    assert result.view is view
    assert repository.subscription_count == 0
