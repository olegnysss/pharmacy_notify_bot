from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pharmacy_bot.application.onboarding import (
    OnboardingResult,
    OnboardingService,
    OnboardingView,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.product_selection import (
    DiscoveryResponse,
    ProductCandidate,
    ProductDraft,
    ProductDraftStatus,
    ProductInputMode,
)


class ProductSelectionView(StrEnum):
    ONBOARDING = "onboarding"
    CHOOSE_METHOD = "choose_method"
    AWAITING_INPUT = "awaiting_input"
    INPUT_ERROR = "input_error"
    RESULTS = "results"
    NO_RESULTS = "no_results"
    TEMPORARY_ERROR = "temporary_error"
    CONFIRMATION = "confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class ProductSelectionResult:
    view: ProductSelectionView
    onboarding: OnboardingResult
    draft: ProductDraft | None = None
    candidates: tuple[ProductCandidate, ...] = ()
    page: int = 0
    total_pages: int = 0
    error: str | None = None


class ProductDraftRepository(Protocol):
    async def start_or_resume(
        self,
        user_id: int,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft: ...

    async def choose_input(
        self,
        user_id: int,
        mode: ProductInputMode,
        *,
        expected_generation: int,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None: ...

    async def choose_methods(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None: ...

    async def get(self, user_id: int) -> ProductDraft | None: ...

    async def begin_discovery(
        self,
        user_id: int,
        *,
        query_text: str,
        source_host: str | None,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None: ...

    async def complete_discovery(
        self,
        user_id: int,
        *,
        generation: int,
        response: DiscoveryResponse,
        now: datetime,
    ) -> ProductDraft | None: ...

    async def select_candidate(
        self,
        user_id: int,
        *,
        generation: int,
        ordinal: int,
        now: datetime,
    ) -> ProductDraft | None: ...

    async def show_results(
        self,
        user_id: int,
        *,
        generation: int,
        now: datetime,
    ) -> ProductDraft | None: ...

    async def confirm_candidate(
        self,
        user_id: int,
        *,
        generation: int,
        ordinal: int,
        now: datetime,
    ) -> ProductDraft | None: ...

    async def cancel(
        self,
        user_id: int,
        *,
        expected_generation: int | None,
        now: datetime,
    ) -> ProductDraft | None: ...


class ProductDiscoveryGateway(Protocol):
    async def search(self, query: str) -> DiscoveryResponse: ...

    async def resolve_link(self, source_host: str, url: str) -> DiscoveryResponse: ...


class ProductLinkPolicy(Protocol):
    def recognize(self, url: str) -> str | None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ProductSelectionService:
    def __init__(
        self,
        onboarding_service: OnboardingService,
        repository: ProductDraftRepository,
        discovery: ProductDiscoveryGateway,
        link_policy: ProductLinkPolicy,
        *,
        query_min_length: int,
        query_max_length: int,
        url_max_length: int,
        page_size: int,
        draft_ttl: timedelta,
        clock: Clock | None = None,
    ) -> None:
        self._onboarding_service = onboarding_service
        self._repository = repository
        self._discovery = discovery
        self._link_policy = link_policy
        self._query_min_length = query_min_length
        self._query_max_length = query_max_length
        self._url_max_length = url_max_length
        self._page_size = page_size
        self._draft_ttl = draft_ttl
        self._clock = clock or SystemClock()

    async def start(self, identity: TelegramIdentity) -> ProductSelectionResult:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        now = self._clock.now()
        draft = await self._repository.start_or_resume(
            onboarding.user.id,
            now=now,
            expires_at=now + self._draft_ttl,
        )
        return self._from_draft(onboarding, draft)

    async def choose_input(
        self,
        identity: TelegramIdentity,
        mode: ProductInputMode,
        *,
        generation: int,
    ) -> ProductSelectionResult:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        now = self._clock.now()
        draft = await self._repository.choose_input(
            onboarding.user.id,
            mode,
            expected_generation=generation,
            now=now,
            expires_at=now + self._draft_ttl,
        )
        if draft is None:
            return self._result(ProductSelectionView.STALE, onboarding)
        return self._result(ProductSelectionView.AWAITING_INPUT, onboarding, draft=draft)

    async def accepts_text(self, identity: TelegramIdentity) -> bool:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return False
        draft = await self._repository.get(onboarding.user.id)
        return bool(
            draft is not None
            and draft.input_mode is not None
            and draft.status
            in {
                ProductDraftStatus.AWAITING_INPUT,
                ProductDraftStatus.RESULTS,
                ProductDraftStatus.NO_RESULTS,
                ProductDraftStatus.ERROR,
            }
            and draft.expires_at > self._clock.now()
        )

    async def choose_methods(
        self,
        identity: TelegramIdentity,
        *,
        generation: int,
    ) -> ProductSelectionResult:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        now = self._clock.now()
        draft = await self._repository.choose_methods(
            onboarding.user.id,
            expected_generation=generation,
            now=now,
            expires_at=now + self._draft_ttl,
        )
        if draft is None:
            return self._result(ProductSelectionView.STALE, onboarding)
        return self._result(ProductSelectionView.CHOOSE_METHOD, onboarding, draft=draft)

    async def submit_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> ProductSelectionResult | None:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        draft = await self._repository.get(onboarding.user.id)
        if draft is None or draft.status not in {
            ProductDraftStatus.AWAITING_INPUT,
            ProductDraftStatus.RESULTS,
            ProductDraftStatus.NO_RESULTS,
            ProductDraftStatus.ERROR,
        }:
            return None
        if draft.input_mode is None:
            return self._result(ProductSelectionView.STALE, onboarding, draft=draft)

        normalized = self._normalize(text)
        source_host: str | None = None
        if draft.input_mode is ProductInputMode.SEARCH:
            error = self._validate_query(normalized)
            if error is not None:
                return self._result(
                    ProductSelectionView.INPUT_ERROR,
                    onboarding,
                    draft=draft,
                    error=error,
                )
        else:
            if not normalized or len(normalized) > self._url_max_length:
                return self._result(
                    ProductSelectionView.INPUT_ERROR,
                    onboarding,
                    draft=draft,
                    error=f"Ссылка должна быть не длиннее {self._url_max_length} символов.",
                )
            source_host = self._link_policy.recognize(normalized)
            if source_host is None:
                return self._result(
                    ProductSelectionView.INPUT_ERROR,
                    onboarding,
                    draft=draft,
                    error=(
                        "Поддерживается только HTTPS-ссылка разрешённой аптечной сети "
                        "без логина, нестандартного порта и редиректа."
                    ),
                )

        now = self._clock.now()
        searching = await self._repository.begin_discovery(
            onboarding.user.id,
            query_text=normalized,
            source_host=source_host,
            now=now,
            expires_at=now + self._draft_ttl,
        )
        if searching is None:
            return self._result(ProductSelectionView.STALE, onboarding, draft=draft)

        response = (
            await self._discovery.search(normalized)
            if draft.input_mode is ProductInputMode.SEARCH
            else await self._discovery.resolve_link(source_host or "", normalized)
        )
        completed = await self._repository.complete_discovery(
            onboarding.user.id,
            generation=searching.generation,
            response=response,
            now=self._clock.now(),
        )
        if completed is None:
            return self._result(ProductSelectionView.STALE, onboarding)
        return self._from_draft(onboarding, completed)

    async def show_page(
        self,
        identity: TelegramIdentity,
        *,
        generation: int,
        page: int,
    ) -> ProductSelectionResult:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        draft = await self._repository.get(onboarding.user.id)
        if (
            draft is None
            or draft.generation != generation
            or draft.status is not ProductDraftStatus.RESULTS
        ):
            return self._result(ProductSelectionView.STALE, onboarding, draft=draft)
        return self._page_result(onboarding, draft, page)

    async def select_candidate(
        self,
        identity: TelegramIdentity,
        *,
        generation: int,
        ordinal: int,
    ) -> ProductSelectionResult:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        draft = await self._repository.select_candidate(
            onboarding.user.id,
            generation=generation,
            ordinal=ordinal,
            now=self._clock.now(),
        )
        if draft is None:
            return self._result(ProductSelectionView.STALE, onboarding)
        return self._result(ProductSelectionView.CONFIRMATION, onboarding, draft=draft)

    async def show_results(
        self,
        identity: TelegramIdentity,
        *,
        generation: int,
    ) -> ProductSelectionResult:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        draft = await self._repository.show_results(
            onboarding.user.id,
            generation=generation,
            now=self._clock.now(),
        )
        if draft is None:
            return self._result(ProductSelectionView.STALE, onboarding)
        selected = draft.selected_ordinal or 0
        return self._page_result(onboarding, draft, selected // self._page_size)

    async def confirm_candidate(
        self,
        identity: TelegramIdentity,
        *,
        generation: int,
        ordinal: int,
    ) -> ProductSelectionResult:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        draft = await self._repository.confirm_candidate(
            onboarding.user.id,
            generation=generation,
            ordinal=ordinal,
            now=self._clock.now(),
        )
        if draft is None:
            return self._result(ProductSelectionView.STALE, onboarding)
        return self._result(ProductSelectionView.CONFIRMED, onboarding, draft=draft)

    async def cancel(
        self,
        identity: TelegramIdentity,
        *,
        generation: int | None = None,
    ) -> ProductSelectionResult:
        onboarding = await self._onboarding_service.start(identity)
        if not self._has_access(onboarding):
            return self._result(ProductSelectionView.ONBOARDING, onboarding)

        draft = await self._repository.cancel(
            onboarding.user.id,
            expected_generation=generation,
            now=self._clock.now(),
        )
        if generation is not None and draft is None:
            return self._result(ProductSelectionView.STALE, onboarding)
        return self._result(ProductSelectionView.CANCELLED, onboarding, draft=draft)

    def _from_draft(
        self,
        onboarding: OnboardingResult,
        draft: ProductDraft,
    ) -> ProductSelectionResult:
        if draft.status is ProductDraftStatus.CHOOSE_METHOD:
            return self._result(ProductSelectionView.CHOOSE_METHOD, onboarding, draft=draft)
        if draft.status is ProductDraftStatus.AWAITING_INPUT:
            return self._result(ProductSelectionView.AWAITING_INPUT, onboarding, draft=draft)
        if draft.status is ProductDraftStatus.RESULTS:
            return self._page_result(onboarding, draft, 0)
        if draft.status is ProductDraftStatus.NO_RESULTS:
            return self._result(ProductSelectionView.NO_RESULTS, onboarding, draft=draft)
        if draft.status is ProductDraftStatus.CONFIRMATION:
            return self._result(ProductSelectionView.CONFIRMATION, onboarding, draft=draft)
        if draft.status is ProductDraftStatus.CONFIRMED:
            return self._result(ProductSelectionView.CONFIRMED, onboarding, draft=draft)
        if draft.status is ProductDraftStatus.CANCELLED:
            return self._result(ProductSelectionView.CANCELLED, onboarding, draft=draft)
        if draft.status is ProductDraftStatus.ERROR:
            return self._result(ProductSelectionView.TEMPORARY_ERROR, onboarding, draft=draft)
        return self._result(ProductSelectionView.STALE, onboarding, draft=draft)

    def _page_result(
        self,
        onboarding: OnboardingResult,
        draft: ProductDraft,
        page: int,
    ) -> ProductSelectionResult:
        total_pages = max(
            1,
            (len(draft.candidates) + self._page_size - 1) // self._page_size,
        )
        if page < 0 or page >= total_pages:
            return self._result(ProductSelectionView.STALE, onboarding, draft=draft)
        start = page * self._page_size
        return self._result(
            ProductSelectionView.RESULTS,
            onboarding,
            draft=draft,
            candidates=draft.candidates[start : start + self._page_size],
            page=page,
            total_pages=total_pages,
        )

    def _validate_query(self, query: str) -> str | None:
        if len(query) < self._query_min_length:
            return f"Введите минимум {self._query_min_length} символа."
        if len(query) > self._query_max_length:
            return f"Сократите запрос до {self._query_max_length} символов."
        if not any(character.isalnum() for character in query):
            return "Добавьте в запрос название товара буквами или цифрами."
        return None

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).split())

    @staticmethod
    def _has_access(onboarding: OnboardingResult) -> bool:
        return onboarding.view is OnboardingView.MAIN_MENU

    @staticmethod
    def _result(
        view: ProductSelectionView,
        onboarding: OnboardingResult,
        *,
        draft: ProductDraft | None = None,
        candidates: tuple[ProductCandidate, ...] = (),
        page: int = 0,
        total_pages: int = 0,
        error: str | None = None,
    ) -> ProductSelectionResult:
        return ProductSelectionResult(
            view=view,
            onboarding=onboarding,
            draft=draft,
            candidates=candidates,
            page=page,
            total_pages=total_pages,
            error=error,
        )
