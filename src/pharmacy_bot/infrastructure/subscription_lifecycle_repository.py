from __future__ import annotations

from datetime import datetime
from typing import ClassVar, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.subscription_lifecycle import SourceCapabilities
from pharmacy_bot.domain.subscription_lifecycle import (
    EditStatus,
    LifecycleAction,
    LifecycleTransition,
    SubscriptionEditDraft,
)
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    MonitoringFilters,
    SourceOption,
    Subscription,
    SubscriptionStatus,
)
from pharmacy_bot.infrastructure.models import (
    AuditLogModel,
    SubscriptionEditDraftModel,
    SubscriptionModel,
)
from pharmacy_bot.infrastructure.subscription_repository import (
    SqlAlchemySubscriptionRepository,
)


class SqlAlchemySubscriptionLifecycleRepository:
    _RESUMABLE: ClassVar[set[EditStatus]] = {
        EditStatus.CHOOSE_BLOCK,
        EditStatus.AWAITING_LOCATION,
        EditStatus.CONFIRM_LOCATION,
        EditStatus.CHOOSE_RADIUS,
        EditStatus.CHOOSE_SOURCES,
        EditStatus.CHOOSE_FILTERS,
        EditStatus.CHOOSE_COMPLETION,
        EditStatus.AWAITING_END_DATE,
        EditStatus.REVIEW,
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_owned_including_deleted(
        self,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None:
        async with self._session_factory() as session:
            model = await self._subscription(session, user_id, subscription_id)
            return self._subscription_snapshot(model) if model else None

    async def start_edit(
        self,
        user_id: int,
        subscription: Subscription,
        sources: tuple[SourceOption, ...],
        *,
        now: datetime,
        expires_at: datetime,
    ) -> SubscriptionEditDraft:
        async with self._session_factory.begin() as session:
            source_values = tuple(
                SourceOption(
                    item.code,
                    item.name,
                    item.available,
                    item.unavailable_reason,
                    item.supports_price,
                    item.supports_low_stock,
                    item.supports_orderable,
                    index,
                )
                for index, item in enumerate(sources)
            )
            await session.execute(
                insert(SubscriptionEditDraftModel)
                .values(
                    user_id=user_id,
                    subscription_id=subscription.id,
                    generation=1,
                    schema_version=1,
                    status=EditStatus.CHOOSE_BLOCK.value,
                    base_updated_at=subscription.updated_at or subscription.created_at,
                    location_candidates=[],
                    location=self._location_to_json(subscription.location),
                    radius_meters=subscription.radius_meters,
                    available_sources=[self._source_to_json(item) for item in source_values],
                    selected_source_codes=list(subscription.source_codes),
                    notify_low_stock=subscription.filters.notify_low_stock,
                    notify_orderable=subscription.filters.notify_orderable,
                    include_price=subscription.filters.include_price,
                    completion_mode=subscription.completion_mode.value,
                    ends_at=subscription.ends_at,
                    idempotency_key=uuid4().hex,
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(index_elements=[SubscriptionEditDraftModel.user_id])
            )
            model = await self._edit_locked(session, user_id)
            if model is None:
                raise RuntimeError("subscription edit draft was not created")
            if (
                model.subscription_id == subscription.id
                and EditStatus(model.status) in self._RESUMABLE
                and model.expires_at > now
            ):
                return self._edit_snapshot(model, subscription)
            self._reset(model, subscription, source_values, expires_at)
            await session.flush()
            return self._edit_snapshot(model, subscription)

    async def get_edit(self, user_id: int) -> SubscriptionEditDraft | None:
        async with self._session_factory() as session:
            model = await self._edit(session, user_id)
            if model is None:
                return None
            subscription = await self._subscription(
                session,
                user_id,
                model.subscription_id,
            )
            return (
                self._edit_snapshot(model, self._subscription_snapshot(subscription))
                if subscription
                else None
            )

    async def save_edit(
        self,
        draft: SubscriptionEditDraft,
        *,
        expected_generation: int,
    ) -> SubscriptionEditDraft | None:
        async with self._session_factory.begin() as session:
            model = await self._edit_locked(session, draft.user_id)
            if model is None or model.generation != expected_generation:
                return None
            subscription = await self._subscription(
                session,
                draft.user_id,
                model.subscription_id,
            )
            if subscription is None:
                return None
            self._apply_draft(model, draft)
            model.generation += 1
            await session.flush()
            return self._edit_snapshot(
                model,
                self._subscription_snapshot(subscription),
            )

    async def apply_edit(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
    ) -> tuple[SubscriptionEditDraft, LifecycleTransition] | None:
        async with self._session_factory.begin() as session:
            draft = await self._edit_locked(session, user_id)
            if draft is None:
                return None
            subscription = await self._subscription_locked(
                session,
                user_id,
                draft.subscription_id,
            )
            if subscription is None:
                return None
            current = self._subscription_snapshot(subscription)
            if EditStatus(draft.status) is EditStatus.APPLIED:
                return self._edit_snapshot(draft, current), LifecycleTransition(
                    LifecycleAction.ALREADY_APPLIED,
                    current,
                )
            if (
                draft.generation != expected_generation
                or EditStatus(draft.status) is not EditStatus.REVIEW
                or subscription.updated_at != draft.base_updated_at
            ):
                return self._edit_snapshot(draft, current), LifecycleTransition(
                    LifecycleAction.STALE,
                    current,
                )
            active_sources = {
                str(item["code"]) for item in draft.available_sources if bool(item["available"])
            }
            if (
                not draft.selected_source_codes
                or not set(draft.selected_source_codes) <= active_sources
                or (
                    CompletionMode(draft.completion_mode) is CompletionMode.UNTIL_DATE
                    and draft.ends_at is None
                )
            ):
                return self._edit_snapshot(draft, current), LifecycleTransition(
                    LifecycleAction.CONFIGURATION_INVALID,
                    current,
                )
            location = self._location_from_json(draft.location)
            scope_changed = (
                subscription.location_key != location.key
                or subscription.radius_meters != draft.radius_meters
                or set(subscription.source_codes) != set(draft.selected_source_codes)
            )
            subscription.location_kind = location.kind.value
            subscription.location_key = location.key
            subscription.location_display_name = location.display_name
            subscription.location_city = location.city
            subscription.location_address = location.address
            subscription.location_latitude = location.latitude
            subscription.location_longitude = location.longitude
            subscription.radius_meters = draft.radius_meters
            subscription.source_codes = list(draft.selected_source_codes)
            subscription.notify_low_stock = draft.notify_low_stock
            subscription.notify_orderable = draft.notify_orderable
            subscription.include_price = draft.include_price
            subscription.completion_mode = draft.completion_mode
            subscription.ends_at = draft.ends_at
            subscription.updated_at = now
            subscription.next_manual_check_at = None
            subscription.manual_check_in_progress = False
            if scope_changed:
                subscription.availability_state = AvailabilityState.UNKNOWN.value
                subscription.state_updated_at = now
                subscription.last_successful_check_at = None
                subscription.freshness_expires_at = None
                subscription.state_source_name = None
                subscription.has_partial_source_error = False
            draft.status = EditStatus.APPLIED.value
            draft.generation += 1
            session.add(
                self._audit(
                    user_id,
                    subscription.id,
                    "subscription_edited",
                    now,
                    {"scope_changed": scope_changed},
                )
            )
            await session.flush()
            await session.refresh(subscription)
            updated = self._subscription_snapshot(subscription)
            return self._edit_snapshot(
                draft,
                updated,
                applied=updated,
            ), LifecycleTransition(LifecycleAction.EDITED, updated)

    async def pause(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
    ) -> LifecycleTransition:
        async with self._session_factory.begin() as session:
            model = await self._subscription_locked(session, user_id, subscription_id)
            if model is None or SubscriptionStatus(model.status) is SubscriptionStatus.DELETED:
                return LifecycleTransition(LifecycleAction.NOT_FOUND)
            status = SubscriptionStatus(model.status)
            if status is SubscriptionStatus.PAUSED:
                return LifecycleTransition(
                    LifecycleAction.ALREADY_APPLIED,
                    self._subscription_snapshot(model),
                )
            if status is not SubscriptionStatus.ACTIVE:
                return LifecycleTransition(
                    LifecycleAction.INVALID_STATE,
                    self._subscription_snapshot(model),
                )
            model.status = SubscriptionStatus.PAUSED.value
            model.manual_check_in_progress = False
            model.next_manual_check_at = None
            model.updated_at = now
            session.add(self._audit(user_id, model.id, "subscription_paused", now))
            await session.flush()
            await session.refresh(model)
            return LifecycleTransition(
                LifecycleAction.PAUSED,
                self._subscription_snapshot(model),
            )

    async def resume(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
    ) -> LifecycleTransition:
        async with self._session_factory.begin() as session:
            model = await self._subscription_locked(session, user_id, subscription_id)
            if model is None or SubscriptionStatus(model.status) is SubscriptionStatus.DELETED:
                return LifecycleTransition(LifecycleAction.NOT_FOUND)
            status = SubscriptionStatus(model.status)
            if status is SubscriptionStatus.ACTIVE:
                return LifecycleTransition(
                    LifecycleAction.ALREADY_APPLIED,
                    self._subscription_snapshot(model),
                )
            if status is not SubscriptionStatus.PAUSED:
                return LifecycleTransition(
                    LifecycleAction.INVALID_STATE,
                    self._subscription_snapshot(model),
                )
            model.status = SubscriptionStatus.ACTIVE.value
            model.availability_state = AvailabilityState.UNKNOWN.value
            model.state_updated_at = now
            model.freshness_expires_at = None
            model.next_manual_check_at = None
            model.updated_at = now
            session.add(self._audit(user_id, model.id, "subscription_resumed", now))
            await session.flush()
            await session.refresh(model)
            return LifecycleTransition(
                LifecycleAction.RESUMED,
                self._subscription_snapshot(model),
            )

    async def delete(
        self,
        user_id: int,
        subscription_id: int,
        *,
        expected_version: int,
        now: datetime,
    ) -> LifecycleTransition:
        async with self._session_factory.begin() as session:
            model = await self._subscription_locked(session, user_id, subscription_id)
            if model is None:
                return LifecycleTransition(LifecycleAction.NOT_FOUND)
            if SubscriptionStatus(model.status) is SubscriptionStatus.DELETED:
                return LifecycleTransition(
                    LifecycleAction.ALREADY_APPLIED,
                    self._subscription_snapshot(model),
                )
            version = int((model.updated_at or model.created_at).timestamp())
            if version != expected_version:
                return LifecycleTransition(
                    LifecycleAction.STALE,
                    self._subscription_snapshot(model),
                )
            model.status = SubscriptionStatus.DELETED.value
            model.manual_check_in_progress = False
            model.next_manual_check_at = None
            model.updated_at = now
            session.add(
                self._audit(
                    user_id,
                    model.id,
                    "subscription_deleted",
                    now,
                    {"soft_delete": True},
                )
            )
            await session.flush()
            await session.refresh(model)
            return LifecycleTransition(
                LifecycleAction.DELETED,
                self._subscription_snapshot(model),
            )

    @staticmethod
    def _reset(
        model: SubscriptionEditDraftModel,
        subscription: Subscription,
        sources: tuple[SourceOption, ...],
        expires_at: datetime,
    ) -> None:
        model.subscription_id = subscription.id
        model.generation += 1
        model.schema_version = 1
        model.status = EditStatus.CHOOSE_BLOCK.value
        model.base_updated_at = subscription.updated_at or subscription.created_at
        model.location_mode = None
        model.location_candidates = []
        model.location = SqlAlchemySubscriptionLifecycleRepository._location_to_json(
            subscription.location
        )
        model.radius_meters = subscription.radius_meters
        model.available_sources = [
            SqlAlchemySubscriptionLifecycleRepository._source_to_json(item) for item in sources
        ]
        model.selected_source_codes = list(subscription.source_codes)
        model.notify_low_stock = subscription.filters.notify_low_stock
        model.notify_orderable = subscription.filters.notify_orderable
        model.include_price = subscription.filters.include_price
        model.completion_mode = subscription.completion_mode.value
        model.ends_at = subscription.ends_at
        model.idempotency_key = uuid4().hex
        model.expires_at = expires_at

    @staticmethod
    def _apply_draft(
        model: SubscriptionEditDraftModel,
        draft: SubscriptionEditDraft,
    ) -> None:
        model.status = draft.status.value
        model.location_mode = draft.location_mode.value if draft.location_mode else None
        model.location_candidates = [
            SqlAlchemySubscriptionLifecycleRepository._location_to_json(item)
            for item in draft.location_candidates
        ]
        model.location = SqlAlchemySubscriptionLifecycleRepository._location_to_json(draft.location)
        model.radius_meters = draft.radius_meters
        model.available_sources = [
            SqlAlchemySubscriptionLifecycleRepository._source_to_json(item)
            for item in draft.available_sources
        ]
        model.selected_source_codes = list(draft.selected_source_codes)
        model.notify_low_stock = draft.filters.notify_low_stock
        model.notify_orderable = draft.filters.notify_orderable
        model.include_price = draft.filters.include_price
        model.completion_mode = draft.completion_mode.value
        model.ends_at = draft.ends_at
        model.expires_at = draft.expires_at

    @classmethod
    def _edit_snapshot(
        cls,
        model: SubscriptionEditDraftModel,
        subscription: Subscription,
        *,
        applied: Subscription | None = None,
    ) -> SubscriptionEditDraft:
        return SubscriptionEditDraft(
            id=model.id,
            user_id=model.user_id,
            subscription_id=model.subscription_id,
            generation=model.generation,
            status=EditStatus(model.status),
            base_updated_at=model.base_updated_at,
            original=subscription,
            location_mode=(LocationInputMode(model.location_mode) if model.location_mode else None),
            location_candidates=tuple(
                cls._location_from_json(item) for item in model.location_candidates
            ),
            location=cls._location_from_json(model.location),
            radius_meters=model.radius_meters,
            available_sources=tuple(
                cls._source_from_json(item) for item in model.available_sources
            ),
            selected_source_codes=tuple(model.selected_source_codes),
            filters=MonitoringFilters(
                model.notify_low_stock,
                model.notify_orderable,
                model.include_price,
            ),
            completion_mode=CompletionMode(model.completion_mode),
            ends_at=model.ends_at,
            idempotency_key=model.idempotency_key,
            expires_at=model.expires_at,
            applied_subscription=applied,
        )

    @staticmethod
    def _location_to_json(value: LocationCandidate) -> dict[str, object]:
        return {
            "key": value.key,
            "kind": value.kind.value,
            "display_name": value.display_name,
            "city": value.city,
            "address": value.address,
            "latitude": value.latitude,
            "longitude": value.longitude,
            "confidence": value.confidence.value,
            "ordinal": value.ordinal,
        }

    @staticmethod
    def _location_from_json(value: dict[str, object]) -> LocationCandidate:
        return LocationCandidate(
            key=str(value["key"]),
            kind=LocationInputMode(str(value["kind"])),
            display_name=str(value["display_name"]),
            city=cast(str | None, value.get("city")),
            address=cast(str | None, value.get("address")),
            latitude=cast(float | None, value.get("latitude")),
            longitude=cast(float | None, value.get("longitude")),
            confidence=LocationConfidence(str(value["confidence"])),
            ordinal=cast(int | None, value.get("ordinal")),
        )

    @staticmethod
    def _source_to_json(value: SourceOption) -> dict[str, object]:
        return {
            "code": value.code,
            "name": value.name,
            "available": value.available,
            "unavailable_reason": value.unavailable_reason,
            "supports_price": value.supports_price,
            "supports_low_stock": value.supports_low_stock,
            "supports_orderable": value.supports_orderable,
            "ordinal": value.ordinal,
        }

    @staticmethod
    def _source_from_json(value: dict[str, object]) -> SourceOption:
        return SourceOption(
            code=str(value["code"]),
            name=str(value["name"]),
            available=bool(value["available"]),
            unavailable_reason=cast(str | None, value.get("unavailable_reason")),
            supports_price=bool(value.get("supports_price")),
            supports_low_stock=bool(value.get("supports_low_stock")),
            supports_orderable=bool(value.get("supports_orderable")),
            ordinal=cast(int | None, value.get("ordinal")),
        )

    @staticmethod
    def _subscription_snapshot(model: SubscriptionModel) -> Subscription:
        return SqlAlchemySubscriptionRepository._snapshot(model)

    @staticmethod
    async def _subscription(
        session: AsyncSession,
        user_id: int,
        subscription_id: int,
    ) -> SubscriptionModel | None:
        statement = select(SubscriptionModel).where(
            SubscriptionModel.id == subscription_id,
            SubscriptionModel.user_id == user_id,
        )
        return cast(SubscriptionModel | None, await session.scalar(statement))

    @staticmethod
    async def _subscription_locked(
        session: AsyncSession,
        user_id: int,
        subscription_id: int,
    ) -> SubscriptionModel | None:
        statement = (
            select(SubscriptionModel)
            .where(
                SubscriptionModel.id == subscription_id,
                SubscriptionModel.user_id == user_id,
            )
            .with_for_update()
        )
        return cast(SubscriptionModel | None, await session.scalar(statement))

    @staticmethod
    async def _edit(
        session: AsyncSession,
        user_id: int,
    ) -> SubscriptionEditDraftModel | None:
        statement = select(SubscriptionEditDraftModel).where(
            SubscriptionEditDraftModel.user_id == user_id
        )
        return cast(SubscriptionEditDraftModel | None, await session.scalar(statement))

    @staticmethod
    async def _edit_locked(
        session: AsyncSession,
        user_id: int,
    ) -> SubscriptionEditDraftModel | None:
        statement = (
            select(SubscriptionEditDraftModel)
            .where(SubscriptionEditDraftModel.user_id == user_id)
            .with_for_update()
        )
        return cast(SubscriptionEditDraftModel | None, await session.scalar(statement))

    @staticmethod
    def _audit(
        user_id: int,
        subscription_id: int,
        action: str,
        occurred_at: datetime,
        metadata: dict[str, object] | None = None,
    ) -> AuditLogModel:
        return AuditLogModel(
            user_id=user_id,
            subscription_id=subscription_id,
            action=action,
            metadata_json=metadata or {},
            occurred_at=occurred_at,
        )


class SourceConfigurationValidator:
    def __init__(self, capabilities: SourceCapabilities) -> None:
        self._capabilities = capabilities

    async def can_resume(self, subscription: Subscription) -> bool:
        available_sources = await self._capabilities.available_sources(
            subscription.product,
            subscription.location,
        )
        active = {item.code for item in available_sources if item.available}
        return bool(
            subscription.source_codes
            and set(subscription.source_codes) <= active
            and subscription.location.key
        )
