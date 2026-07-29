from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.geocoding import ProviderGeocodingResult
from pharmacy_bot.domain.geocoding import (
    ConfirmedGeocoding,
    GeocodingCandidate,
    GeocodingConflict,
    GeocodingDecision,
    GeocodingPrecision,
    GeocodingResult,
)
from pharmacy_bot.domain.geography import Coordinate
from pharmacy_bot.infrastructure.models import GeocodingSessionModel


class SqlAlchemyGeocodingSessionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_candidates(
        self,
        user_id: int,
        generation: int,
        query_hash: str,
        locale: str,
        region_hint_hash: str | None,
        provider_result: ProviderGeocodingResult,
        candidates: tuple[GeocodingCandidate, ...],
        decision: GeocodingDecision,
        *,
        expires_at: datetime,
        now: datetime,
    ) -> GeocodingResult:
        payload = [self._candidate_payload(item) for item in candidates]
        async with self._session_factory.begin() as session:
            await session.execute(
                insert(GeocodingSessionModel)
                .values(
                    user_id=user_id,
                    generation=generation,
                    query_hash=query_hash,
                    locale=locale,
                    region_hint_hash=region_hint_hash,
                    provider_code=provider_result.provider_code,
                    provider_data_version=provider_result.data_version,
                    status=decision.value,
                    candidates=payload,
                    expires_at=expires_at,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        GeocodingSessionModel.user_id,
                        GeocodingSessionModel.generation,
                    ]
                )
            )
            model = await session.scalar(
                select(GeocodingSessionModel).where(
                    GeocodingSessionModel.user_id == user_id,
                    GeocodingSessionModel.generation == generation,
                )
            )
            if model is None:
                raise RuntimeError("geocoding session was not created")
            restored = tuple(self._candidate(item) for item in model.candidates)
            status = (
                GeocodingDecision.AMBIGUOUS
                if model.status == "confirmed"
                else GeocodingDecision(model.status)
            )
            return GeocodingResult(status, generation, restored, model.expires_at)

    async def confirm(
        self,
        user_id: int,
        generation: int,
        candidate_id: str,
        *,
        now: datetime,
    ) -> ConfirmedGeocoding:
        async with self._session_factory.begin() as session:
            model = await session.scalar(
                select(GeocodingSessionModel)
                .where(
                    GeocodingSessionModel.user_id == user_id,
                    GeocodingSessionModel.generation == generation,
                )
                .with_for_update()
            )
            if model is None or model.expires_at < now:
                raise GeocodingConflict("geocoding selection is unavailable")
            candidate = next(
                (
                    self._candidate(item)
                    for item in model.candidates
                    if item.get("candidate_id") == candidate_id
                ),
                None,
            )
            if candidate is None:
                raise GeocodingConflict("geocoding selection is unavailable")
            if model.selected_candidate_id not in {None, candidate_id}:
                raise GeocodingConflict("another geocoding candidate is already confirmed")
            model.selected_candidate_id = candidate_id
            model.status = "confirmed"
            model.updated_at = now
            return ConfirmedGeocoding(
                model.id,
                user_id,
                generation,
                candidate,
                model.provider_data_version,
                model.updated_at,
            )

    @staticmethod
    def _candidate_payload(item: GeocodingCandidate) -> dict[str, object]:
        return {
            "candidate_id": item.candidate_id,
            "provider_code": item.provider_code,
            "external_id": item.external_id,
            "normalized_address": item.normalized_address,
            "latitude": str(item.coordinate.latitude),
            "longitude": str(item.coordinate.longitude),
            "precision": item.precision.value,
        }

    @staticmethod
    def _candidate(value: dict[str, object]) -> GeocodingCandidate:
        return GeocodingCandidate(
            candidate_id=str(value["candidate_id"]),
            provider_code=str(value["provider_code"]),
            external_id=str(value["external_id"]),
            normalized_address=str(value["normalized_address"]),
            coordinate=Coordinate(
                Decimal(str(value["latitude"])),
                Decimal(str(value["longitude"])),
            ),
            precision=GeocodingPrecision(str(value["precision"])),
        )
