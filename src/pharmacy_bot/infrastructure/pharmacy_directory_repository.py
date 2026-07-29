from __future__ import annotations

import base64
import math
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.geography import distance_meters
from pharmacy_bot.domain.geography import Coordinate
from pharmacy_bot.domain.pharmacy_directory import (
    Pharmacy,
    PharmacyDirectoryConflict,
    PharmacyIdentity,
    PharmacyKind,
    PharmacyMappingActor,
    PharmacyMatchLevel,
    PharmacyMatchResult,
    PharmacyPage,
    PharmacyPageItem,
    PharmacyStatus,
    SourcePharmacy,
    StalePharmacyMapping,
)
from pharmacy_bot.infrastructure.models import (
    PharmacyMappingDecisionModel,
    PharmacyModel,
    PharmacyVersionModel,
    SourcePharmacyModel,
    SourcePharmacyVersionModel,
)


class SqlAlchemyPharmacyDirectoryRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_or_get_pharmacy(
        self,
        identity: PharmacyIdentity,
        status: PharmacyStatus,
        fingerprint: str,
        *,
        now: datetime,
    ) -> Pharmacy:
        async with self._session_factory.begin() as session:
            pharmacy_id = await session.scalar(
                insert(PharmacyModel)
                .values(
                    version=1,
                    status=status.value,
                    fingerprint=fingerprint,
                    **self._identity_values(identity),
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[PharmacyModel.fingerprint])
                .returning(PharmacyModel.id)
            )
            created = pharmacy_id is not None
            if pharmacy_id is None:
                pharmacy_id = await session.scalar(
                    select(PharmacyModel.id).where(PharmacyModel.fingerprint == fingerprint)
                )
            if pharmacy_id is None:
                raise RuntimeError("pharmacy was not created or found")
            model = await session.get(PharmacyModel, pharmacy_id)
            if model is None:
                raise RuntimeError("pharmacy disappeared")
            if created:
                session.add(
                    PharmacyVersionModel(
                        pharmacy_id=model.id,
                        version=1,
                        fingerprint=fingerprint,
                        safe_snapshot=self._safe_snapshot(identity, status),
                        created_at=now,
                    )
                )
            return self._pharmacy(model)

    async def upsert_source(
        self,
        source_code: str,
        external_id: str,
        identity: PharmacyIdentity,
        status: PharmacyStatus,
        fingerprint: str,
        *,
        now: datetime,
    ) -> SourcePharmacy:
        values = {
            "status": status.value,
            "fingerprint": fingerprint,
            **self._identity_values(identity),
        }
        async with self._session_factory.begin() as session:
            source_id = await session.scalar(
                insert(SourcePharmacyModel)
                .values(
                    source_code=source_code,
                    external_id=external_id,
                    version=1,
                    mapping_version=0,
                    **values,
                    first_seen_at=now,
                    last_seen_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        SourcePharmacyModel.source_code,
                        SourcePharmacyModel.external_id,
                    ]
                )
                .returning(SourcePharmacyModel.id)
            )
            created = source_id is not None
            model = await self._source_locked(session, source_code, external_id)
            if model is None:
                raise RuntimeError("source pharmacy was not created or found")
            if created:
                self._add_source_version(
                    session, model, ("created",), identity, status, fingerprint, now
                )
            elif model.fingerprint != fingerprint and now >= model.updated_at:
                changed = tuple(
                    sorted(key for key, value in values.items() if getattr(model, key) != value)
                )
                for key, value in values.items():
                    setattr(model, key, value)
                model.version += 1
                model.updated_at = now
                model.last_seen_at = max(model.last_seen_at, now)
                self._add_source_version(
                    session, model, changed, identity, status, fingerprint, now
                )
            else:
                model.last_seen_at = max(model.last_seen_at, now)
            await session.flush()
            await session.refresh(model)
            return self._source(model)

    async def confirm_mapping(
        self,
        source_pharmacy_id: int,
        canonical_pharmacy_id: int,
        result: PharmacyMatchResult,
        actor: PharmacyMappingActor,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> SourcePharmacy:
        async with self._session_factory.begin() as session:
            model = await self._source_by_id_locked(session, source_pharmacy_id)
            if model is None or await session.get(PharmacyModel, canonical_pharmacy_id) is None:
                raise PharmacyDirectoryConflict("pharmacy mapping target does not exist")
            existing = await session.scalar(
                select(PharmacyMappingDecisionModel).where(
                    PharmacyMappingDecisionModel.actor_internal_id == actor.internal_id,
                    PharmacyMappingDecisionModel.idempotency_key == idempotency_key,
                )
            )
            if existing:
                if (
                    existing.action != "confirm"
                    or existing.source_pharmacy_id != source_pharmacy_id
                    or existing.canonical_pharmacy_id != canonical_pharmacy_id
                ):
                    raise PharmacyDirectoryConflict(
                        "idempotency key belongs to another pharmacy decision"
                    )
                return self._source(model)
            model.mapping_version += 1
            model.canonical_pharmacy_id = canonical_pharmacy_id
            model.mapping_level = result.level.value
            session.add(
                PharmacyMappingDecisionModel(
                    source_pharmacy_id=source_pharmacy_id,
                    canonical_pharmacy_id=canonical_pharmacy_id,
                    action="confirm",
                    match_level=result.level.value,
                    reasons=list(result.reasons),
                    algorithm_version=result.algorithm_version,
                    mapping_version=model.mapping_version,
                    actor_internal_id=actor.internal_id,
                    idempotency_key=idempotency_key,
                    created_at=now,
                )
            )
            await session.flush()
            return self._source(model)

    async def revoke_mapping(
        self,
        source_pharmacy_id: int,
        expected_mapping_version: int,
        actor: PharmacyMappingActor,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> SourcePharmacy:
        async with self._session_factory.begin() as session:
            model = await self._source_by_id_locked(session, source_pharmacy_id)
            if model is None:
                raise PharmacyDirectoryConflict("source pharmacy does not exist")
            existing = await session.scalar(
                select(PharmacyMappingDecisionModel).where(
                    PharmacyMappingDecisionModel.actor_internal_id == actor.internal_id,
                    PharmacyMappingDecisionModel.idempotency_key == idempotency_key,
                )
            )
            if existing:
                if existing.action != "revoke" or existing.source_pharmacy_id != source_pharmacy_id:
                    raise PharmacyDirectoryConflict(
                        "idempotency key belongs to another pharmacy decision"
                    )
                return self._source(model)
            if model.mapping_version != expected_mapping_version:
                raise StalePharmacyMapping
            model.mapping_version += 1
            previous_id = model.canonical_pharmacy_id
            model.canonical_pharmacy_id = None
            model.mapping_level = None
            session.add(
                PharmacyMappingDecisionModel(
                    source_pharmacy_id=source_pharmacy_id,
                    canonical_pharmacy_id=previous_id,
                    action="revoke",
                    match_level=None,
                    reasons=["operator_revoked"],
                    algorithm_version="pharmacy-match-v1",
                    mapping_version=model.mapping_version,
                    actor_internal_id=actor.internal_id,
                    idempotency_key=idempotency_key,
                    created_at=now,
                )
            )
            await session.flush()
            return self._source(model)

    async def search_radius(
        self,
        center: Coordinate,
        radius_meters: int,
        *,
        after_distance: int | None,
        after_id: int | None,
        limit: int,
    ) -> PharmacyPage:
        latitude_delta = Decimal(str(radius_meters / 111_320))
        cosine = abs(math.cos(math.radians(float(center.latitude))))
        longitude_delta = (
            Decimal("180") if cosine < 0.0001 else Decimal(str(radius_meters / (111_320 * cosine)))
        )
        conditions = [
            PharmacyModel.status == PharmacyStatus.ACTIVE.value,
            PharmacyModel.latitude.is_not(None),
            PharmacyModel.longitude.is_not(None),
            PharmacyModel.latitude >= center.latitude - latitude_delta,
            PharmacyModel.latitude <= center.latitude + latitude_delta,
        ]
        if longitude_delta < Decimal("180"):
            conditions.extend(
                (
                    PharmacyModel.longitude >= center.longitude - longitude_delta,
                    PharmacyModel.longitude <= center.longitude + longitude_delta,
                )
            )
        async with self._session_factory() as session:
            models = list((await session.scalars(select(PharmacyModel).where(*conditions))).all())
        items = []
        for model in models:
            if model.latitude is None or model.longitude is None:
                continue
            distance = distance_meters(
                center,
                Coordinate(model.latitude, model.longitude),
            )
            if distance <= radius_meters:
                items.append(PharmacyPageItem(self._pharmacy(model), distance))
        items.sort(key=lambda item: (item.distance_meters, item.pharmacy.id))
        if after_distance is not None and after_id is not None:
            items = [
                item
                for item in items
                if (item.distance_meters, item.pharmacy.id) > (after_distance, after_id)
            ]
        has_more = len(items) > limit
        visible = items[:limit]
        cursor = (
            self._cursor(visible[-1].distance_meters, visible[-1].pharmacy.id)
            if has_more and visible
            else None
        )
        return PharmacyPage(tuple(visible), cursor)

    @staticmethod
    def _identity_values(identity: PharmacyIdentity) -> dict[str, object]:
        return {
            "kind": identity.kind.value,
            "name": identity.name,
            "normalized_address": identity.normalized_address,
            "network_key": identity.network_key,
            "latitude": identity.coordinate.latitude if identity.coordinate else None,
            "longitude": identity.coordinate.longitude if identity.coordinate else None,
            "trusted_identifier": identity.trusted_identifier,
        }

    @classmethod
    def _safe_snapshot(
        cls, identity: PharmacyIdentity, status: PharmacyStatus
    ) -> dict[str, object]:
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in {
                **cls._identity_values(identity),
                "status": status.value,
            }.items()
        }

    @classmethod
    def _add_source_version(
        cls,
        session: AsyncSession,
        model: SourcePharmacyModel,
        changed: tuple[str, ...],
        identity: PharmacyIdentity,
        status: PharmacyStatus,
        fingerprint: str,
        now: datetime,
    ) -> None:
        session.add(
            SourcePharmacyVersionModel(
                source_pharmacy_id=model.id,
                version=model.version,
                fingerprint=fingerprint,
                safe_snapshot=cls._safe_snapshot(identity, status),
                changed_fields=list(changed),
                observed_at=now,
            )
        )

    @staticmethod
    def _pharmacy(model: PharmacyModel) -> Pharmacy:
        return Pharmacy(
            model.id,
            model.version,
            PharmacyIdentity(
                model.name,
                model.normalized_address,
                model.network_key,
                (
                    Coordinate(model.latitude, model.longitude)
                    if model.latitude is not None and model.longitude is not None
                    else None
                ),
                PharmacyKind(model.kind),
                model.trusted_identifier,
            ),
            PharmacyStatus(model.status),
            model.fingerprint,
            model.created_at,
            model.updated_at,
        )

    @staticmethod
    def _source(model: SourcePharmacyModel) -> SourcePharmacy:
        return SourcePharmacy(
            model.id,
            model.source_code,
            model.external_id,
            PharmacyIdentity(
                model.name,
                model.normalized_address,
                model.network_key,
                (
                    Coordinate(model.latitude, model.longitude)
                    if model.latitude is not None and model.longitude is not None
                    else None
                ),
                PharmacyKind(model.kind),
                model.trusted_identifier,
            ),
            PharmacyStatus(model.status),
            model.canonical_pharmacy_id,
            PharmacyMatchLevel(model.mapping_level) if model.mapping_level else None,
            model.mapping_version,
            model.version,
            model.fingerprint,
            model.first_seen_at,
            model.last_seen_at,
        )

    @staticmethod
    async def _source_locked(
        session: AsyncSession, source_code: str, external_id: str
    ) -> SourcePharmacyModel | None:
        return cast(
            SourcePharmacyModel | None,
            await session.scalar(
                select(SourcePharmacyModel)
                .where(
                    SourcePharmacyModel.source_code == source_code,
                    SourcePharmacyModel.external_id == external_id,
                )
                .with_for_update()
            ),
        )

    @staticmethod
    async def _source_by_id_locked(
        session: AsyncSession, source_id: int
    ) -> SourcePharmacyModel | None:
        return cast(
            SourcePharmacyModel | None,
            await session.scalar(
                select(SourcePharmacyModel)
                .where(SourcePharmacyModel.id == source_id)
                .with_for_update()
            ),
        )

    @staticmethod
    def _cursor(distance: int, pharmacy_id: int) -> str:
        return base64.urlsafe_b64encode(f"{distance}:{pharmacy_id}".encode()).decode().rstrip("=")
