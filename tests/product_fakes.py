from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from pharmacy_bot.domain.product_selection import (
    DiscoveryResponse,
    DiscoveryStatus,
    ProductDraft,
    ProductDraftStatus,
    ProductInputMode,
)


class InMemoryProductDraftRepository:
    def __init__(self) -> None:
        self.drafts: dict[int, ProductDraft] = {}
        self.subscription_count = 0
        self._next_id = 1

    async def start_or_resume(
        self,
        user_id: int,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft:
        draft = self.drafts.get(user_id)
        active = {
            ProductDraftStatus.CHOOSE_METHOD,
            ProductDraftStatus.AWAITING_INPUT,
            ProductDraftStatus.RESULTS,
            ProductDraftStatus.NO_RESULTS,
            ProductDraftStatus.CONFIRMATION,
            ProductDraftStatus.ERROR,
        }
        if draft is not None and draft.status in active and draft.expires_at > now:
            return draft

        generation = draft.generation + 1 if draft else 1
        created = ProductDraft(
            id=draft.id if draft else self._next_id,
            user_id=user_id,
            generation=generation,
            status=ProductDraftStatus.CHOOSE_METHOD,
            input_mode=None,
            query_text=None,
            source_host=None,
            candidates=(),
            selected_ordinal=None,
            selected_candidate_version=None,
            expires_at=expires_at,
        )
        if draft is None:
            self._next_id += 1
        self.drafts[user_id] = created
        return created

    async def choose_input(
        self,
        user_id: int,
        mode: ProductInputMode,
        *,
        expected_generation: int,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None:
        draft = self.drafts.get(user_id)
        if draft is None or draft.generation != expected_generation or draft.expires_at <= now:
            return None
        updated = replace(
            draft,
            generation=draft.generation + 1,
            status=ProductDraftStatus.AWAITING_INPUT,
            input_mode=mode,
            query_text=None,
            source_host=None,
            candidates=(),
            selected_ordinal=None,
            selected_candidate_version=None,
            expires_at=expires_at,
        )
        self.drafts[user_id] = updated
        return updated

    async def choose_methods(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None:
        draft = self.drafts.get(user_id)
        if draft is None or draft.generation != expected_generation or draft.expires_at <= now:
            return None
        updated = replace(
            draft,
            generation=draft.generation + 1,
            status=ProductDraftStatus.CHOOSE_METHOD,
            input_mode=None,
            query_text=None,
            source_host=None,
            candidates=(),
            selected_ordinal=None,
            selected_candidate_version=None,
            expires_at=expires_at,
        )
        self.drafts[user_id] = updated
        return updated

    async def get(self, user_id: int) -> ProductDraft | None:
        return self.drafts.get(user_id)

    async def begin_discovery(
        self,
        user_id: int,
        *,
        query_text: str,
        source_host: str | None,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None:
        draft = self.drafts.get(user_id)
        if (
            draft is None
            or draft.input_mode is None
            or draft.expires_at <= now
            or draft.status
            not in {
                ProductDraftStatus.AWAITING_INPUT,
                ProductDraftStatus.RESULTS,
                ProductDraftStatus.NO_RESULTS,
                ProductDraftStatus.ERROR,
            }
        ):
            return None
        updated = replace(
            draft,
            generation=draft.generation + 1,
            status=ProductDraftStatus.SEARCHING,
            query_text=query_text,
            source_host=source_host,
            candidates=(),
            selected_ordinal=None,
            selected_candidate_version=None,
            expires_at=expires_at,
        )
        self.drafts[user_id] = updated
        return updated

    async def complete_discovery(
        self,
        user_id: int,
        *,
        generation: int,
        response: DiscoveryResponse,
        now: datetime,
    ) -> ProductDraft | None:
        draft = self.drafts.get(user_id)
        if (
            draft is None
            or draft.generation != generation
            or draft.status is not ProductDraftStatus.SEARCHING
            or draft.expires_at <= now
        ):
            return None
        candidates = tuple(
            replace(candidate, ordinal=ordinal)
            for ordinal, candidate in enumerate(response.candidates)
        )
        if response.status is DiscoveryStatus.SUCCESS and candidates:
            status = ProductDraftStatus.RESULTS
        elif response.status in {DiscoveryStatus.SUCCESS, DiscoveryStatus.EMPTY}:
            status = ProductDraftStatus.NO_RESULTS
        else:
            status = ProductDraftStatus.ERROR
        updated = replace(draft, status=status, candidates=candidates)
        self.drafts[user_id] = updated
        return updated

    async def select_candidate(
        self,
        user_id: int,
        *,
        generation: int,
        ordinal: int,
        now: datetime,
    ) -> ProductDraft | None:
        draft = self.drafts.get(user_id)
        if (
            draft is None
            or draft.generation != generation
            or draft.status is not ProductDraftStatus.RESULTS
            or draft.expires_at <= now
        ):
            return None
        candidate = next(
            (item for item in draft.candidates if item.ordinal == ordinal),
            None,
        )
        if candidate is None:
            return None
        updated = replace(
            draft,
            status=ProductDraftStatus.CONFIRMATION,
            selected_ordinal=ordinal,
            selected_candidate_version=candidate.version,
        )
        self.drafts[user_id] = updated
        return updated

    async def show_results(
        self,
        user_id: int,
        *,
        generation: int,
        now: datetime,
    ) -> ProductDraft | None:
        draft = self.drafts.get(user_id)
        if (
            draft is None
            or draft.generation != generation
            or draft.status is not ProductDraftStatus.CONFIRMATION
            or draft.expires_at <= now
        ):
            return None
        updated = replace(draft, status=ProductDraftStatus.RESULTS)
        self.drafts[user_id] = updated
        return updated

    async def confirm_candidate(
        self,
        user_id: int,
        *,
        generation: int,
        ordinal: int,
        now: datetime,
    ) -> ProductDraft | None:
        draft = self.drafts.get(user_id)
        if (
            draft is None
            or draft.generation != generation
            or draft.status is not ProductDraftStatus.CONFIRMATION
            or draft.selected_ordinal != ordinal
            or draft.expires_at <= now
        ):
            return None
        candidate = next(
            (item for item in draft.candidates if item.ordinal == ordinal),
            None,
        )
        if candidate is None or candidate.version != draft.selected_candidate_version:
            return None
        updated = replace(draft, status=ProductDraftStatus.CONFIRMED)
        self.drafts[user_id] = updated
        return updated

    async def cancel(
        self,
        user_id: int,
        *,
        expected_generation: int | None,
        now: datetime,
    ) -> ProductDraft | None:
        draft = self.drafts.get(user_id)
        if draft is None or (
            expected_generation is not None and draft.generation != expected_generation
        ):
            return None
        updated = replace(
            draft,
            generation=draft.generation + 1,
            status=ProductDraftStatus.CANCELLED,
            input_mode=None,
            query_text=None,
            source_host=None,
            candidates=(),
            selected_ordinal=None,
            selected_candidate_version=None,
            expires_at=now,
        )
        self.drafts[user_id] = updated
        return updated


class FakeProductDiscoveryGateway:
    def __init__(
        self,
        *,
        search_response: DiscoveryResponse,
        link_response: DiscoveryResponse | None = None,
    ) -> None:
        self.search_response = search_response
        self.link_response = link_response or search_response
        self.search_calls: list[str] = []
        self.link_calls: list[tuple[str, str]] = []

    async def search(self, query: str) -> DiscoveryResponse:
        self.search_calls.append(query)
        return self.search_response

    async def resolve_link(self, source_host: str, url: str) -> DiscoveryResponse:
        self.link_calls.append((source_host, url))
        return self.link_response
