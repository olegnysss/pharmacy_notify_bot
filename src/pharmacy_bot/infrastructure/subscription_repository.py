from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.subscriptions import (
    CheckGate,
    CheckGateResult,
    SubscriptionFilter,
    SubscriptionPage,
)
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    MonitoringFilters,
    ProductSnapshot,
    Subscription,
    SubscriptionStatus,
)
from pharmacy_bot.infrastructure.models import SubscriptionModel


class SqlAlchemySubscriptionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_owned(
        self,
        user_id: int,
        selected_filter: SubscriptionFilter,
        *,
        page: int,
        page_size: int,
    ) -> SubscriptionPage:
        async with self._session_factory() as session:
            conditions = [
                SubscriptionModel.user_id == user_id,
                SubscriptionModel.status != SubscriptionStatus.DELETED.value,
            ]
            if selected_filter is not SubscriptionFilter.ALL:
                conditions.append(SubscriptionModel.status == selected_filter.value)
            total = int(
                await session.scalar(
                    select(func.count()).select_from(SubscriptionModel).where(*conditions)
                )
                or 0
            )
            total_pages = max(1, (total + page_size - 1) // page_size)
            safe_page = max(0, min(page, total_pages - 1))
            statement = (
                select(SubscriptionModel)
                .where(*conditions)
                .order_by(SubscriptionModel.created_at.desc(), SubscriptionModel.id.desc())
                .offset(safe_page * page_size)
                .limit(page_size)
            )
            models = tuple((await session.scalars(statement)).all())
            latest = await session.scalar(
                select(func.max(SubscriptionModel.updated_at)).where(*conditions)
            )
            version = int(latest.timestamp()) if latest else 0
            return SubscriptionPage(
                items=tuple(self._snapshot(item) for item in models),
                page=safe_page,
                total_pages=total_pages,
                total_items=total,
                version=version,
            )

    async def get_owned(
        self,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None:
        async with self._session_factory() as session:
            model = await self._owned(session, user_id, subscription_id)
            return self._snapshot(model) if model else None

    async def begin_manual_check(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
        cooldown: timedelta,
    ) -> CheckGateResult:
        async with self._session_factory.begin() as session:
            model = await self._owned_locked(session, user_id, subscription_id)
            if model is None:
                return CheckGateResult(CheckGate.NOT_FOUND)
            snapshot = self._snapshot(model)
            if SubscriptionStatus(model.status) is not SubscriptionStatus.ACTIVE:
                return CheckGateResult(CheckGate.NOT_ACTIVE, snapshot)
            if (
                model.freshness_expires_at is not None
                and model.freshness_expires_at > now
                and AvailabilityState(model.availability_state)
                not in {AvailabilityState.PENDING, AvailabilityState.UNKNOWN}
            ):
                return CheckGateResult(CheckGate.CACHED, snapshot)
            if model.manual_check_in_progress:
                return CheckGateResult(CheckGate.IN_PROGRESS, snapshot)
            if model.next_manual_check_at is not None and model.next_manual_check_at > now:
                return CheckGateResult(
                    CheckGate.RATE_LIMITED,
                    snapshot,
                    model.next_manual_check_at,
                )
            model.manual_check_in_progress = True
            model.next_manual_check_at = now + cooldown
            model.updated_at = now
            await session.flush()
            await session.refresh(model)
            return CheckGateResult(CheckGate.ACCEPTED, self._snapshot(model))

    async def mark_manual_check_failed(
        self,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None:
        async with self._session_factory.begin() as session:
            model = await self._owned_locked(session, user_id, subscription_id)
            if model is None:
                return None
            model.manual_check_in_progress = False
            await session.flush()
            await session.refresh(model)
            return self._snapshot(model)

    @staticmethod
    async def _owned(
        session: AsyncSession,
        user_id: int,
        subscription_id: int,
    ) -> SubscriptionModel | None:
        statement = select(SubscriptionModel).where(
            SubscriptionModel.id == subscription_id,
            SubscriptionModel.user_id == user_id,
            SubscriptionModel.status != SubscriptionStatus.DELETED.value,
        )
        return cast(SubscriptionModel | None, await session.scalar(statement))

    @staticmethod
    async def _owned_locked(
        session: AsyncSession,
        user_id: int,
        subscription_id: int,
    ) -> SubscriptionModel | None:
        statement = (
            select(SubscriptionModel)
            .where(
                SubscriptionModel.id == subscription_id,
                SubscriptionModel.user_id == user_id,
                SubscriptionModel.status != SubscriptionStatus.DELETED.value,
            )
            .with_for_update()
        )
        return cast(SubscriptionModel | None, await session.scalar(statement))

    @staticmethod
    def _snapshot(model: SubscriptionModel) -> Subscription:
        return Subscription(
            id=model.id,
            user_id=model.user_id,
            product=ProductSnapshot(
                model.product_candidate_key,
                model.product_version,
                model.product_name,
                model.product_form,
                model.product_dosage,
                model.product_package,
                model.product_manufacturer,
                model.product_source_host,
            ),
            location=LocationCandidate(
                model.location_key,
                LocationInputMode(model.location_kind),
                model.location_display_name,
                city=model.location_city,
                address=model.location_address,
                latitude=model.location_latitude,
                longitude=model.location_longitude,
                confidence=LocationConfidence.EXACT,
            ),
            radius_meters=model.radius_meters,
            source_codes=tuple(model.source_codes),
            filters=MonitoringFilters(
                model.notify_low_stock,
                model.notify_orderable,
                model.include_price,
            ),
            completion_mode=CompletionMode(model.completion_mode),
            ends_at=model.ends_at,
            status=SubscriptionStatus(model.status),
            availability_state=AvailabilityState(model.availability_state),
            created_at=model.created_at,
            updated_at=model.updated_at,
            state_updated_at=model.state_updated_at,
            last_successful_check_at=model.last_successful_check_at,
            freshness_expires_at=model.freshness_expires_at,
            state_source_name=model.state_source_name,
            has_partial_source_error=model.has_partial_source_error,
            manual_check_in_progress=model.manual_check_in_progress,
            next_manual_check_at=model.next_manual_check_at,
        )
