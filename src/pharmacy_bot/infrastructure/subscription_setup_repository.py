from __future__ import annotations

from datetime import datetime
from typing import ClassVar, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.domain.subscription_setup import (
    ActiveSubscriptionLimitReached,
    AvailabilityState,
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    MonitoringFilters,
    ProductSnapshot,
    SetupStatus,
    SourceOption,
    Subscription,
    SubscriptionSetupDraft,
    SubscriptionStatus,
)
from pharmacy_bot.infrastructure.models import (
    SubscriptionModel,
    SubscriptionSetupDraftModel,
    UserModel,
)


class SqlAlchemySubscriptionSetupRepository:
    _RESUMABLE: ClassVar[set[SetupStatus]] = {
        SetupStatus.CHOOSE_LOCATION,
        SetupStatus.AWAITING_LOCATION,
        SetupStatus.CONFIRM_LOCATION,
        SetupStatus.CHOOSE_RADIUS,
        SetupStatus.CHOOSE_SOURCES,
        SetupStatus.CHOOSE_FILTERS,
        SetupStatus.CHOOSE_COMPLETION,
        SetupStatus.AWAITING_END_DATE,
        SetupStatus.REVIEW,
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def start_or_resume(
        self,
        user_id: int,
        product: ProductSnapshot,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> SubscriptionSetupDraft:
        async with self._session_factory.begin() as session:
            await session.execute(
                insert(SubscriptionSetupDraftModel)
                .values(
                    user_id=user_id,
                    generation=1,
                    schema_version=1,
                    status=SetupStatus.CHOOSE_LOCATION.value,
                    **self._product_values(product),
                    location_candidates=[],
                    available_sources=[],
                    selected_source_codes=[],
                    notify_low_stock=False,
                    notify_orderable=False,
                    include_price=False,
                    idempotency_key=uuid4().hex,
                    expires_at=expires_at,
                )
                .on_conflict_do_nothing(index_elements=[SubscriptionSetupDraftModel.user_id])
            )
            model = await self._get_locked(session, user_id)
            if model is None:
                raise RuntimeError("subscription setup draft was not created")
            same_product = (
                model.product_candidate_key == product.candidate_key
                and model.product_version == product.version
            )
            if (
                same_product
                and SetupStatus(model.status) in self._RESUMABLE
                and model.expires_at > now
            ):
                return self._snapshot(model)
            self._reset(model, product, expires_at)
            await session.flush()
            return self._snapshot(model)

    async def get(self, user_id: int) -> SubscriptionSetupDraft | None:
        async with self._session_factory() as session:
            model = await self._get(session, user_id)
            if model is None:
                return None
            subscription = await self._subscription_by_key(session, model.idempotency_key)
            return self._snapshot(
                model,
                subscription.id if subscription is not None else None,
            )

    async def save(
        self,
        draft: SubscriptionSetupDraft,
        *,
        expected_generation: int,
    ) -> SubscriptionSetupDraft | None:
        async with self._session_factory.begin() as session:
            model = await self._get_locked(session, draft.user_id)
            if model is None or model.generation != expected_generation:
                return None
            self._apply(model, draft)
            model.generation += 1
            await session.flush()
            return self._snapshot(model)

    async def create_subscription(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
        max_active_subscriptions: int = 20,
    ) -> tuple[SubscriptionSetupDraft, Subscription] | None:
        async with self._session_factory.begin() as session:
            model = await self._get_locked(session, user_id)
            if model is None:
                return None
            existing = await self._subscription_by_key(session, model.idempotency_key)
            if existing is not None:
                return self._snapshot(model, existing.id), self._subscription_snapshot(existing)
            await session.scalar(
                select(UserModel.id).where(UserModel.id == user_id).with_for_update()
            )
            active_count = await session.scalar(
                select(func.count())
                .select_from(SubscriptionModel)
                .where(
                    SubscriptionModel.user_id == user_id,
                    SubscriptionModel.status == SubscriptionStatus.ACTIVE.value,
                )
            )
            if int(active_count or 0) >= max_active_subscriptions:
                raise ActiveSubscriptionLimitReached
            if (
                model.generation != expected_generation
                or SetupStatus(model.status) is not SetupStatus.REVIEW
                or model.location is None
                or model.radius_meters is None
                or not model.selected_source_codes
                or model.completion_mode is None
                or model.expires_at <= now
            ):
                return None
            location = self._location_from_json(model.location)
            subscription = SubscriptionModel(
                setup_draft_id=model.id,
                user_id=user_id,
                creation_key=model.idempotency_key,
                **self._product_values(self._product_snapshot(model)),
                canonical_product_id=model.canonical_product_id,
                canonical_product_version=model.canonical_product_version,
                location_kind=location.kind.value,
                location_key=location.key,
                location_display_name=location.display_name,
                location_city=location.city,
                location_address=location.address,
                location_latitude=location.latitude,
                location_longitude=location.longitude,
                radius_meters=model.radius_meters,
                source_codes=list(model.selected_source_codes),
                notify_low_stock=model.notify_low_stock,
                notify_orderable=model.notify_orderable,
                include_price=model.include_price,
                completion_mode=model.completion_mode,
                ends_at=model.ends_at,
                status=SubscriptionStatus.ACTIVE.value,
                availability_state=AvailabilityState.PENDING.value,
                created_at=now,
                updated_at=now,
            )
            session.add(subscription)
            model.status = SetupStatus.CREATED.value
            model.generation += 1
            await session.flush()
            return self._snapshot(model, subscription.id), self._subscription_snapshot(subscription)

    @staticmethod
    def _reset(
        model: SubscriptionSetupDraftModel,
        product: ProductSnapshot,
        expires_at: datetime,
    ) -> None:
        model.generation += 1
        model.schema_version = 1
        model.status = SetupStatus.CHOOSE_LOCATION.value
        for key, value in SqlAlchemySubscriptionSetupRepository._product_values(product).items():
            setattr(model, key, value)
        model.location_mode = None
        model.canonical_product_id = None
        model.canonical_product_version = None
        model.location_candidates = []
        model.location = None
        model.radius_meters = None
        model.available_sources = []
        model.selected_source_codes = []
        model.notify_low_stock = False
        model.notify_orderable = False
        model.include_price = False
        model.completion_mode = None
        model.ends_at = None
        model.idempotency_key = uuid4().hex
        model.expires_at = expires_at

    @staticmethod
    def _apply(
        model: SubscriptionSetupDraftModel,
        draft: SubscriptionSetupDraft,
    ) -> None:
        model.status = draft.status.value
        model.location_mode = draft.location_mode.value if draft.location_mode else None
        model.location_candidates = [
            SqlAlchemySubscriptionSetupRepository._location_to_json(item)
            for item in draft.location_candidates
        ]
        model.location = (
            SqlAlchemySubscriptionSetupRepository._location_to_json(draft.location)
            if draft.location
            else None
        )
        model.radius_meters = draft.radius_meters
        model.available_sources = [
            SqlAlchemySubscriptionSetupRepository._source_to_json(item)
            for item in draft.available_sources
        ]
        model.selected_source_codes = list(draft.selected_source_codes)
        model.notify_low_stock = draft.filters.notify_low_stock
        model.notify_orderable = draft.filters.notify_orderable
        model.include_price = draft.filters.include_price
        model.completion_mode = draft.completion_mode.value if draft.completion_mode else None
        model.ends_at = draft.ends_at
        model.expires_at = draft.expires_at

    @staticmethod
    def _product_values(product: ProductSnapshot) -> dict[str, object]:
        return {
            "product_candidate_key": product.candidate_key,
            "product_version": product.version,
            "product_name": product.name,
            "product_form": product.form,
            "product_dosage": product.dosage,
            "product_package": product.package,
            "product_manufacturer": product.manufacturer,
            "product_source_host": product.source_host,
        }

    @staticmethod
    def _location_to_json(location: LocationCandidate) -> dict[str, object]:
        return {
            "key": location.key,
            "kind": location.kind.value,
            "display_name": location.display_name,
            "city": location.city,
            "address": location.address,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "confidence": location.confidence.value,
            "ordinal": location.ordinal,
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
    def _source_to_json(source: SourceOption) -> dict[str, object]:
        return {
            "code": source.code,
            "name": source.name,
            "available": source.available,
            "unavailable_reason": source.unavailable_reason,
            "supports_price": source.supports_price,
            "supports_low_stock": source.supports_low_stock,
            "supports_orderable": source.supports_orderable,
            "ordinal": source.ordinal,
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
    def _product_snapshot(model: SubscriptionSetupDraftModel) -> ProductSnapshot:
        return ProductSnapshot(
            candidate_key=model.product_candidate_key,
            version=model.product_version,
            name=model.product_name,
            form=model.product_form,
            dosage=model.product_dosage,
            package=model.product_package,
            manufacturer=model.product_manufacturer,
            source_host=model.product_source_host,
        )

    @classmethod
    def _snapshot(
        cls,
        model: SubscriptionSetupDraftModel,
        subscription_id: int | None = None,
    ) -> SubscriptionSetupDraft:
        return SubscriptionSetupDraft(
            id=model.id,
            user_id=model.user_id,
            generation=model.generation,
            status=SetupStatus(model.status),
            product=cls._product_snapshot(model),
            location_mode=LocationInputMode(model.location_mode) if model.location_mode else None,
            location_candidates=tuple(
                cls._location_from_json(item) for item in model.location_candidates
            ),
            location=cls._location_from_json(model.location) if model.location else None,
            radius_meters=model.radius_meters,
            available_sources=tuple(
                cls._source_from_json(item) for item in model.available_sources
            ),
            selected_source_codes=tuple(model.selected_source_codes),
            filters=MonitoringFilters(
                notify_low_stock=model.notify_low_stock,
                notify_orderable=model.notify_orderable,
                include_price=model.include_price,
            ),
            completion_mode=(
                CompletionMode(model.completion_mode) if model.completion_mode else None
            ),
            ends_at=model.ends_at,
            idempotency_key=model.idempotency_key,
            expires_at=model.expires_at,
            subscription_id=subscription_id,
        )

    @staticmethod
    def _subscription_snapshot(model: SubscriptionModel) -> Subscription:
        return Subscription(
            id=model.id,
            user_id=model.user_id,
            product=ProductSnapshot(
                candidate_key=model.product_candidate_key,
                version=model.product_version,
                name=model.product_name,
                form=model.product_form,
                dosage=model.product_dosage,
                package=model.product_package,
                manufacturer=model.product_manufacturer,
                source_host=model.product_source_host,
            ),
            location=LocationCandidate(
                key=model.location_key,
                kind=LocationInputMode(model.location_kind),
                display_name=model.location_display_name,
                city=model.location_city,
                address=model.location_address,
                latitude=model.location_latitude,
                longitude=model.location_longitude,
                confidence=LocationConfidence.EXACT,
            ),
            radius_meters=model.radius_meters,
            source_codes=tuple(model.source_codes),
            filters=MonitoringFilters(
                notify_low_stock=model.notify_low_stock,
                notify_orderable=model.notify_orderable,
                include_price=model.include_price,
            ),
            completion_mode=CompletionMode(model.completion_mode),
            ends_at=model.ends_at,
            status=SubscriptionStatus(model.status),
            availability_state=AvailabilityState(model.availability_state),
            created_at=model.created_at,
        )

    @staticmethod
    async def _get(
        session: AsyncSession,
        user_id: int,
    ) -> SubscriptionSetupDraftModel | None:
        statement = select(SubscriptionSetupDraftModel).where(
            SubscriptionSetupDraftModel.user_id == user_id
        )
        return cast(SubscriptionSetupDraftModel | None, await session.scalar(statement))

    @staticmethod
    async def _get_locked(
        session: AsyncSession,
        user_id: int,
    ) -> SubscriptionSetupDraftModel | None:
        statement = (
            select(SubscriptionSetupDraftModel)
            .where(SubscriptionSetupDraftModel.user_id == user_id)
            .with_for_update()
        )
        return cast(SubscriptionSetupDraftModel | None, await session.scalar(statement))

    @staticmethod
    async def _subscription_by_key(
        session: AsyncSession,
        creation_key: str,
    ) -> SubscriptionModel | None:
        statement = select(SubscriptionModel).where(SubscriptionModel.creation_key == creation_key)
        return cast(SubscriptionModel | None, await session.scalar(statement))
