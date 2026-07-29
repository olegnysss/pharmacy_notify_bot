from __future__ import annotations

from datetime import time
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    MonitoringFilters,
    SubscriptionStatus,
)
from pharmacy_bot.domain.user_settings import (
    SettingsStatus,
    SupportedLanguage,
    Usage,
    UserPreferences,
)
from pharmacy_bot.infrastructure.models import (
    SubscriptionModel,
    UserPreferencesModel,
)


class SqlAlchemyUserSettingsRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create(self, user_id: int) -> UserPreferences:
        async with self._session_factory.begin() as session:
            await session.execute(
                insert(UserPreferencesModel)
                .values(
                    user_id=user_id,
                    generation=1,
                    language=SupportedLanguage.RU.value,
                    timezone_name="Europe/Moscow",
                    default_source_codes=[],
                    notify_low_stock=False,
                    notify_orderable=False,
                    include_price=False,
                    completion_mode=CompletionMode.CONTINUE.value,
                    quiet_hours_enabled=False,
                    quiet_hours_start=time(22, 0),
                    quiet_hours_end=time(8, 0),
                    digest_enabled=False,
                    max_points_per_message=5,
                    editor_status=SettingsStatus.IDLE.value,
                    editor_location_candidates=[],
                )
                .on_conflict_do_nothing(index_elements=[UserPreferencesModel.user_id])
            )
            model = await self._get(session, user_id)
            if model is None:
                raise RuntimeError("user preferences were not created")
            return self._snapshot(model)

    async def save(
        self,
        preferences: UserPreferences,
        *,
        expected_generation: int,
    ) -> UserPreferences | None:
        async with self._session_factory.begin() as session:
            model = await self._get_locked(session, preferences.user_id)
            if model is None or model.generation != expected_generation:
                return None
            model.language = preferences.language.value
            model.timezone_name = preferences.timezone_name
            model.default_location = (
                self._location_to_json(preferences.default_location)
                if preferences.default_location
                else None
            )
            model.default_radius_meters = preferences.default_radius_meters
            model.default_source_codes = list(preferences.default_source_codes)
            model.notify_low_stock = preferences.filters.notify_low_stock
            model.notify_orderable = preferences.filters.notify_orderable
            model.include_price = preferences.filters.include_price
            model.completion_mode = preferences.completion_mode.value
            model.quiet_hours_enabled = preferences.quiet_hours_enabled
            model.quiet_hours_start = preferences.quiet_hours_start
            model.quiet_hours_end = preferences.quiet_hours_end
            model.digest_enabled = preferences.digest_enabled
            model.max_points_per_message = preferences.max_points_per_message
            model.editor_status = preferences.status.value
            model.editor_location_mode = (
                preferences.location_mode.value if preferences.location_mode else None
            )
            model.editor_location_candidates = [
                self._location_to_json(item) for item in preferences.location_candidates
            ]
            model.generation += 1
            await session.flush()
            return self._snapshot(model)

    async def usage(self, user_id: int, max_active: int) -> Usage:
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(SubscriptionModel)
                .where(
                    SubscriptionModel.user_id == user_id,
                    SubscriptionModel.status == SubscriptionStatus.ACTIVE.value,
                )
            )
            return Usage(int(count or 0), max_active)

    @classmethod
    def _snapshot(cls, model: UserPreferencesModel) -> UserPreferences:
        return UserPreferences(
            user_id=model.user_id,
            generation=model.generation,
            language=SupportedLanguage(model.language),
            timezone_name=model.timezone_name,
            default_location=(
                cls._location_from_json(model.default_location) if model.default_location else None
            ),
            default_radius_meters=model.default_radius_meters,
            default_source_codes=tuple(model.default_source_codes),
            filters=MonitoringFilters(
                model.notify_low_stock,
                model.notify_orderable,
                model.include_price,
            ),
            completion_mode=CompletionMode(model.completion_mode),
            quiet_hours_enabled=model.quiet_hours_enabled,
            quiet_hours_start=model.quiet_hours_start,
            quiet_hours_end=model.quiet_hours_end,
            digest_enabled=model.digest_enabled,
            max_points_per_message=model.max_points_per_message,
            status=SettingsStatus(model.editor_status),
            location_mode=(
                LocationInputMode(model.editor_location_mode)
                if model.editor_location_mode
                else None
            ),
            location_candidates=tuple(
                cls._location_from_json(item) for item in model.editor_location_candidates
            ),
        )

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
    async def _get(
        session: AsyncSession,
        user_id: int,
    ) -> UserPreferencesModel | None:
        return cast(
            UserPreferencesModel | None,
            await session.scalar(
                select(UserPreferencesModel).where(UserPreferencesModel.user_id == user_id)
            ),
        )

    @staticmethod
    async def _get_locked(
        session: AsyncSession,
        user_id: int,
    ) -> UserPreferencesModel | None:
        return cast(
            UserPreferencesModel | None,
            await session.scalar(
                select(UserPreferencesModel)
                .where(UserPreferencesModel.user_id == user_id)
                .with_for_update()
            ),
        )
