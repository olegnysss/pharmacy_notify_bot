from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Protocol

from pharmacy_bot.application.geography import GeographyPolicy, GeographyValidationError
from pharmacy_bot.domain.geocoding import (
    ConfirmedGeocoding,
    GeocodingCandidate,
    GeocodingDecision,
    GeocodingPrecision,
    GeocodingResult,
    GeocodingValidationError,
)
from pharmacy_bot.domain.geography import Coordinate, LocationScopeInput, LocationScopeKind

_ACTIVE_CONTENT = re.compile(r"<\s*/?\s*[a-z]|javascript\s*:", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    external_id: str
    normalized_address: str
    coordinate: Coordinate
    precision: GeocodingPrecision


@dataclass(frozen=True, slots=True)
class ProviderGeocodingResult:
    provider_code: str
    data_version: str
    candidates: tuple[ProviderCandidate, ...]


class TemporaryGeocoderError(Exception):
    pass


class PermanentGeocoderError(Exception):
    pass


class Geocoder(Protocol):
    async def geocode(
        self,
        query: str,
        *,
        locale: str,
        region_hint: str | None,
    ) -> ProviderGeocodingResult: ...


class GeocodingSessionRepository(Protocol):
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
    ) -> GeocodingResult: ...

    async def confirm(
        self,
        user_id: int,
        generation: int,
        candidate_id: str,
        *,
        now: datetime,
    ) -> ConfirmedGeocoding: ...


class GeocodingService:
    def __init__(
        self,
        provider: Geocoder,
        repository: GeocodingSessionRepository,
        geography: GeographyPolicy,
        *,
        max_candidates: int = 8,
        session_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._geography = geography
        self._max_candidates = max_candidates
        self._session_ttl = session_ttl

    async def resolve(
        self,
        user_id: int,
        generation: int,
        query: str,
        *,
        locale: str,
        region_hint: str | None,
        now: datetime,
    ) -> GeocodingResult:
        normalized_query = unicodedata.normalize("NFKC", query).strip()
        if not 3 <= len(normalized_query) <= 256 or _ACTIVE_CONTENT.search(normalized_query):
            raise GeocodingValidationError("address query is invalid")
        if generation <= 0 or user_id <= 0:
            raise GeocodingValidationError("geocoding context is invalid")
        normalized_locale = self._bounded_key(locale, 16, "locale")
        normalized_hint = (
            self._bounded_key(region_hint, 128, "region hint") if region_hint else None
        )
        query_hash = self._hash(
            "\x1f".join((normalized_query.casefold(), normalized_locale, normalized_hint or ""))
        )
        try:
            provider_result = await self._provider.geocode(
                normalized_query,
                locale=normalized_locale,
                region_hint=normalized_hint,
            )
        except TemporaryGeocoderError:
            return GeocodingResult(
                GeocodingDecision.TEMPORARY_ERROR,
                generation,
                (),
                None,
            )
        candidates = self._validate_candidates(
            user_id,
            generation,
            query_hash,
            provider_result,
        )
        if not candidates:
            decision = GeocodingDecision.INSUFFICIENT
        elif len(candidates) == 1 and candidates[0].precision in {
            GeocodingPrecision.ROOFTOP,
            GeocodingPrecision.ADDRESS,
        }:
            decision = GeocodingDecision.EXACT
        else:
            decision = GeocodingDecision.AMBIGUOUS
        return await self._repository.save_candidates(
            user_id,
            generation,
            query_hash,
            normalized_locale,
            self._hash(normalized_hint) if normalized_hint else None,
            provider_result,
            candidates,
            decision,
            expires_at=now + self._session_ttl,
            now=now,
        )

    async def confirm(
        self,
        user_id: int,
        generation: int,
        candidate_id: str,
        *,
        now: datetime,
    ) -> ConfirmedGeocoding:
        if not re.fullmatch(r"[a-f0-9]{24}", candidate_id):
            raise GeocodingValidationError("candidate reference is invalid")
        return await self._repository.confirm(
            user_id,
            generation,
            candidate_id,
            now=now,
        )

    def _validate_candidates(
        self,
        user_id: int,
        generation: int,
        query_hash: str,
        result: ProviderGeocodingResult,
    ) -> tuple[GeocodingCandidate, ...]:
        provider = self._bounded_key(result.provider_code, 64, "provider")
        self._bounded_key(result.data_version, 64, "provider data version")
        if len(result.candidates) > self._max_candidates:
            raise GeocodingValidationError("provider returned too many candidates")
        values: list[GeocodingCandidate] = []
        seen: set[str] = set()
        for item in result.candidates:
            external_id = self._bounded_key(item.external_id, 128, "external result")
            address = unicodedata.normalize("NFKC", item.normalized_address).strip()
            if not address or len(address) > 512 or _ACTIVE_CONTENT.search(address):
                raise GeocodingValidationError("provider address is invalid")
            try:
                coordinate = self._geography.normalize(
                    LocationScopeInput(
                        LocationScopeKind.ADDRESS,
                        coordinate=item.coordinate,
                        address_key="validated",
                    )
                ).coordinate
            except GeographyValidationError as error:
                raise GeocodingValidationError("provider coordinate is invalid") from error
            if external_id in seen or coordinate is None:
                raise GeocodingValidationError("provider candidates are duplicated or invalid")
            seen.add(external_id)
            candidate_id = self._hash(
                f"{user_id}:{generation}:{query_hash}:{provider}:{external_id}"
            )[:24]
            values.append(
                GeocodingCandidate(
                    candidate_id,
                    provider,
                    external_id,
                    address,
                    coordinate,
                    item.precision,
                )
            )
        return tuple(values)

    @staticmethod
    def _bounded_key(value: str, limit: int, label: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip().casefold()
        if not normalized or len(normalized) > limit or not re.fullmatch(r"[\w.-]+", normalized):
            raise GeocodingValidationError(f"{label} is invalid")
        return normalized

    @staticmethod
    def _hash(value: str) -> str:
        return sha256(value.encode()).hexdigest()
