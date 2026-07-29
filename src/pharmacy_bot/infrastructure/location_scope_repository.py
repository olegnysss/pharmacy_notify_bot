from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.domain.geography import (
    Coordinate,
    LocationScope,
    LocationScopeConflict,
    LocationScopeInput,
    LocationScopeKind,
    StaleLocationScopeVersion,
)
from pharmacy_bot.infrastructure.models import (
    LocationScopeModel,
    LocationScopeVersionModel,
)


class SqlAlchemyLocationScopeRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_or_get(
        self,
        value: LocationScopeInput,
        fingerprint: str,
        *,
        now: datetime,
    ) -> LocationScope:
        async with self._session_factory.begin() as session:
            scope_id = await session.scalar(
                insert(LocationScopeModel)
                .values(
                    version=1,
                    fingerprint=fingerprint,
                    **self._values(value),
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[LocationScopeModel.fingerprint])
                .returning(LocationScopeModel.id)
            )
            created = scope_id is not None
            if scope_id is None:
                scope_id = await session.scalar(
                    select(LocationScopeModel.id).where(
                        LocationScopeModel.fingerprint == fingerprint
                    )
                )
            if scope_id is None:
                raise RuntimeError("location scope was not created or found")
            model = await session.get(LocationScopeModel, scope_id)
            if model is None:
                raise RuntimeError("location scope disappeared")
            if created:
                session.add(
                    LocationScopeVersionModel(
                        location_scope_id=model.id,
                        version=1,
                        fingerprint=fingerprint,
                        safe_snapshot=self._snapshot_values(value),
                        created_at=now,
                    )
                )
            return self._snapshot(model)

    async def revise(
        self,
        scope_id: int,
        expected_version: int,
        value: LocationScopeInput,
        fingerprint: str,
        *,
        now: datetime,
    ) -> LocationScope:
        async with self._session_factory.begin() as session:
            model = cast(
                LocationScopeModel | None,
                await session.scalar(
                    select(LocationScopeModel)
                    .where(LocationScopeModel.id == scope_id)
                    .with_for_update()
                ),
            )
            if model is None:
                raise LocationScopeConflict("location scope does not exist")
            if model.version != expected_version:
                raise StaleLocationScopeVersion
            owner = await session.scalar(
                select(LocationScopeModel.id).where(
                    LocationScopeModel.fingerprint == fingerprint,
                    LocationScopeModel.id != scope_id,
                )
            )
            if owner is not None:
                raise LocationScopeConflict("identical scope already exists")
            model.version += 1
            model.fingerprint = fingerprint
            model.updated_at = now
            for key, item in self._values(value).items():
                setattr(model, key, item)
            session.add(
                LocationScopeVersionModel(
                    location_scope_id=model.id,
                    version=model.version,
                    fingerprint=fingerprint,
                    safe_snapshot=self._snapshot_values(value),
                    created_at=now,
                )
            )
            await session.flush()
            await session.refresh(model)
            return self._snapshot(model)

    @staticmethod
    def _values(value: LocationScopeInput) -> dict[str, object]:
        return {
            "kind": value.kind.value,
            "country_key": value.country_key,
            "region_key": value.region_key,
            "city_key": value.city_key,
            "district_key": value.district_key,
            "latitude": value.coordinate.latitude if value.coordinate else None,
            "longitude": value.coordinate.longitude if value.coordinate else None,
            "radius_meters": value.radius_meters,
            "address_key": value.address_key,
            "pharmacy_ids": list(value.pharmacy_ids),
            "online_region_key": value.online_region_key,
        }

    @classmethod
    def _snapshot_values(cls, value: LocationScopeInput) -> dict[str, object]:
        return {
            key: str(item) if isinstance(item, Decimal) else item
            for key, item in cls._values(value).items()
        }

    @staticmethod
    def _snapshot(model: LocationScopeModel) -> LocationScope:
        coordinate = (
            Coordinate(model.latitude, model.longitude)
            if model.latitude is not None and model.longitude is not None
            else None
        )
        return LocationScope(
            id=model.id,
            version=model.version,
            value=LocationScopeInput(
                kind=LocationScopeKind(model.kind),
                country_key=model.country_key,
                region_key=model.region_key,
                city_key=model.city_key,
                district_key=model.district_key,
                coordinate=coordinate,
                radius_meters=model.radius_meters,
                address_key=model.address_key,
                pharmacy_ids=tuple(model.pharmacy_ids),
                online_region_key=model.online_region_key,
            ),
            fingerprint=model.fingerprint,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
