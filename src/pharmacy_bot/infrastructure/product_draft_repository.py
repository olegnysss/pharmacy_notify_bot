from __future__ import annotations

from datetime import datetime
from typing import ClassVar, cast

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from pharmacy_bot.domain.product_selection import (
    DiscoveryResponse,
    DiscoveryStatus,
    MatchConfidence,
    ProductCandidate,
    ProductDraft,
    ProductDraftStatus,
    ProductInputMode,
)
from pharmacy_bot.infrastructure.models import (
    ProductSelectionCandidateModel,
    ProductSelectionDraftModel,
)


class SqlAlchemyProductDraftRepository:
    _ACTIVE_STATUSES: ClassVar[set[ProductDraftStatus]] = {
        ProductDraftStatus.CHOOSE_METHOD,
        ProductDraftStatus.AWAITING_INPUT,
        ProductDraftStatus.RESULTS,
        ProductDraftStatus.NO_RESULTS,
        ProductDraftStatus.CONFIRMATION,
        ProductDraftStatus.ERROR,
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start_or_resume(
        self,
        user_id: int,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft:
        async with self._session_factory.begin() as session:
            draft = await self._ensure_locked(session, user_id, expires_at)
            status = ProductDraftStatus(draft.status)
            if status in self._ACTIVE_STATUSES and draft.expires_at > now:
                return self._snapshot(draft)

            await self._reset(
                session,
                draft,
                status=ProductDraftStatus.CHOOSE_METHOD,
                expires_at=expires_at,
            )
            return self._snapshot(draft)

    async def choose_input(
        self,
        user_id: int,
        mode: ProductInputMode,
        *,
        expected_generation: int,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None:
        async with self._session_factory.begin() as session:
            draft = await self._ensure_locked(session, user_id, expires_at)
            if draft.generation != expected_generation or draft.expires_at <= now:
                return None
            await self._clear_candidates(session, draft)
            draft.generation += 1
            draft.status = ProductDraftStatus.AWAITING_INPUT.value
            draft.input_mode = mode.value
            draft.query_text = None
            draft.source_host = None
            draft.selected_ordinal = None
            draft.selected_candidate_version = None
            draft.expires_at = expires_at
            await session.flush()
            return self._snapshot(draft)

    async def get(self, user_id: int) -> ProductDraft | None:
        async with self._session_factory() as session:
            draft = await self._get(session, user_id)
            return self._snapshot(draft) if draft is not None else None

    async def choose_methods(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None:
        async with self._session_factory.begin() as session:
            draft = await self._get_locked(session, user_id)
            if draft is None or draft.generation != expected_generation or draft.expires_at <= now:
                return None
            await self._reset(
                session,
                draft,
                status=ProductDraftStatus.CHOOSE_METHOD,
                expires_at=expires_at,
            )
            return self._snapshot(draft)

    async def begin_discovery(
        self,
        user_id: int,
        *,
        query_text: str,
        source_host: str | None,
        now: datetime,
        expires_at: datetime,
    ) -> ProductDraft | None:
        async with self._session_factory.begin() as session:
            draft = await self._get_locked(session, user_id)
            if (
                draft is None
                or draft.input_mode is None
                or ProductDraftStatus(draft.status)
                not in {
                    ProductDraftStatus.AWAITING_INPUT,
                    ProductDraftStatus.RESULTS,
                    ProductDraftStatus.NO_RESULTS,
                    ProductDraftStatus.ERROR,
                }
                or draft.expires_at <= now
            ):
                return None

            await self._clear_candidates(session, draft)
            draft.generation += 1
            draft.status = ProductDraftStatus.SEARCHING.value
            draft.query_text = query_text
            draft.source_host = source_host
            draft.selected_ordinal = None
            draft.selected_candidate_version = None
            draft.expires_at = expires_at
            await session.flush()
            return self._snapshot(draft)

    async def complete_discovery(
        self,
        user_id: int,
        *,
        generation: int,
        response: DiscoveryResponse,
        now: datetime,
    ) -> ProductDraft | None:
        async with self._session_factory.begin() as session:
            draft = await self._get_locked(session, user_id)
            if (
                draft is None
                or draft.generation != generation
                or ProductDraftStatus(draft.status) is not ProductDraftStatus.SEARCHING
                or draft.expires_at <= now
            ):
                return None

            if response.status is DiscoveryStatus.SUCCESS and response.candidates:
                draft.candidates.extend(
                    self._candidate_model(draft.id, ordinal, candidate)
                    for ordinal, candidate in enumerate(response.candidates)
                )
                draft.status = ProductDraftStatus.RESULTS.value
            elif response.status in {DiscoveryStatus.SUCCESS, DiscoveryStatus.EMPTY}:
                draft.status = ProductDraftStatus.NO_RESULTS.value
            else:
                draft.status = ProductDraftStatus.ERROR.value
            await session.flush()
            return self._snapshot(draft)

    async def select_candidate(
        self,
        user_id: int,
        *,
        generation: int,
        ordinal: int,
        now: datetime,
    ) -> ProductDraft | None:
        async with self._session_factory.begin() as session:
            draft = await self._get_locked(session, user_id)
            if draft is None or not self._is_current_results(draft, generation, now):
                return None
            candidate = next(
                (item for item in draft.candidates if item.ordinal == ordinal),
                None,
            )
            if candidate is None:
                return None

            draft.status = ProductDraftStatus.CONFIRMATION.value
            draft.selected_ordinal = ordinal
            draft.selected_candidate_version = candidate.version
            await session.flush()
            return self._snapshot(draft)

    async def show_results(
        self,
        user_id: int,
        *,
        generation: int,
        now: datetime,
    ) -> ProductDraft | None:
        async with self._session_factory.begin() as session:
            draft = await self._get_locked(session, user_id)
            if (
                draft is None
                or draft.generation != generation
                or ProductDraftStatus(draft.status) is not ProductDraftStatus.CONFIRMATION
                or draft.expires_at <= now
            ):
                return None
            draft.status = ProductDraftStatus.RESULTS.value
            await session.flush()
            return self._snapshot(draft)

    async def confirm_candidate(
        self,
        user_id: int,
        *,
        generation: int,
        ordinal: int,
        now: datetime,
    ) -> ProductDraft | None:
        async with self._session_factory.begin() as session:
            draft = await self._get_locked(session, user_id)
            if (
                draft is None
                or draft.generation != generation
                or ProductDraftStatus(draft.status) is not ProductDraftStatus.CONFIRMATION
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

            draft.status = ProductDraftStatus.CONFIRMED.value
            await session.flush()
            return self._snapshot(draft)

    async def cancel(
        self,
        user_id: int,
        *,
        expected_generation: int | None,
        now: datetime,
    ) -> ProductDraft | None:
        async with self._session_factory.begin() as session:
            draft = await self._get_locked(session, user_id)
            if draft is None or (
                expected_generation is not None and draft.generation != expected_generation
            ):
                return None
            await self._clear_candidates(session, draft)
            draft.generation += 1
            draft.status = ProductDraftStatus.CANCELLED.value
            draft.input_mode = None
            draft.query_text = None
            draft.source_host = None
            draft.selected_ordinal = None
            draft.selected_candidate_version = None
            draft.expires_at = now
            await session.flush()
            return self._snapshot(draft)

    async def _ensure_locked(
        self,
        session: AsyncSession,
        user_id: int,
        expires_at: datetime,
    ) -> ProductSelectionDraftModel:
        statement = (
            insert(ProductSelectionDraftModel)
            .values(
                user_id=user_id,
                generation=1,
                status=ProductDraftStatus.CHOOSE_METHOD.value,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=[ProductSelectionDraftModel.user_id])
        )
        await session.execute(statement)
        draft = await self._get_locked(session, user_id)
        if draft is None:
            raise RuntimeError("product selection draft was not created")
        return draft

    async def _reset(
        self,
        session: AsyncSession,
        draft: ProductSelectionDraftModel,
        *,
        status: ProductDraftStatus,
        expires_at: datetime,
    ) -> None:
        await self._clear_candidates(session, draft)
        draft.generation += 1
        draft.status = status.value
        draft.input_mode = None
        draft.query_text = None
        draft.source_host = None
        draft.selected_ordinal = None
        draft.selected_candidate_version = None
        draft.expires_at = expires_at
        await session.flush()

    @staticmethod
    async def _clear_candidates(
        session: AsyncSession,
        draft: ProductSelectionDraftModel,
    ) -> None:
        await session.execute(
            delete(ProductSelectionCandidateModel).where(
                ProductSelectionCandidateModel.draft_id == draft.id
            )
        )
        draft.candidates.clear()

    @staticmethod
    async def _get(
        session: AsyncSession,
        user_id: int,
    ) -> ProductSelectionDraftModel | None:
        statement = (
            select(ProductSelectionDraftModel)
            .where(ProductSelectionDraftModel.user_id == user_id)
            .options(selectinload(ProductSelectionDraftModel.candidates))
        )
        return cast(ProductSelectionDraftModel | None, await session.scalar(statement))

    @staticmethod
    async def _get_locked(
        session: AsyncSession,
        user_id: int,
    ) -> ProductSelectionDraftModel | None:
        statement = (
            select(ProductSelectionDraftModel)
            .where(ProductSelectionDraftModel.user_id == user_id)
            .options(selectinload(ProductSelectionDraftModel.candidates))
            .with_for_update()
        )
        return cast(ProductSelectionDraftModel | None, await session.scalar(statement))

    @staticmethod
    def _is_current_results(
        draft: ProductSelectionDraftModel | None,
        generation: int,
        now: datetime,
    ) -> bool:
        return bool(
            draft is not None
            and draft.generation == generation
            and ProductDraftStatus(draft.status) is ProductDraftStatus.RESULTS
            and draft.expires_at > now
        )

    @staticmethod
    def _candidate_model(
        draft_id: int,
        ordinal: int,
        candidate: ProductCandidate,
    ) -> ProductSelectionCandidateModel:
        return ProductSelectionCandidateModel(
            draft_id=draft_id,
            ordinal=ordinal,
            candidate_key=candidate.candidate_key,
            version=candidate.version,
            name=candidate.name,
            form=candidate.form,
            dosage=candidate.dosage,
            package=candidate.package,
            manufacturer=candidate.manufacturer,
            source_name=candidate.source_name,
            source_host=candidate.source_host,
            confidence=candidate.confidence.value,
        )

    @staticmethod
    def _snapshot(model: ProductSelectionDraftModel) -> ProductDraft:
        return ProductDraft(
            id=model.id,
            user_id=model.user_id,
            generation=model.generation,
            status=ProductDraftStatus(model.status),
            input_mode=ProductInputMode(model.input_mode) if model.input_mode else None,
            query_text=model.query_text,
            source_host=model.source_host,
            candidates=tuple(
                ProductCandidate(
                    candidate_key=candidate.candidate_key,
                    version=candidate.version,
                    name=candidate.name,
                    form=candidate.form,
                    dosage=candidate.dosage,
                    package=candidate.package,
                    manufacturer=candidate.manufacturer,
                    source_name=candidate.source_name,
                    source_host=candidate.source_host,
                    confidence=MatchConfidence(candidate.confidence),
                    ordinal=candidate.ordinal,
                )
                for candidate in sorted(model.candidates, key=lambda item: item.ordinal)
            ),
            selected_ordinal=model.selected_ordinal,
            selected_candidate_version=model.selected_candidate_version,
            expires_at=model.expires_at,
        )
