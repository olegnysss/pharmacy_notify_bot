from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Protocol

from pharmacy_bot.domain.geography import (
    Coordinate,
    GeographicDecision,
    GeographicEligibility,
    GeographicReason,
    LocationScope,
    LocationScopeInput,
    LocationScopeKind,
    PointLocation,
)

_KEY = re.compile(r"^[\w.-]{1,128}$", re.UNICODE)
_EARTH_RADIUS_METERS = 6_371_008.8


class GeographyValidationError(ValueError):
    pass


class LocationScopeRepository(Protocol):
    async def create_or_get(
        self,
        value: LocationScopeInput,
        fingerprint: str,
        *,
        now: datetime,
    ) -> LocationScope: ...

    async def revise(
        self,
        scope_id: int,
        expected_version: int,
        value: LocationScopeInput,
        fingerprint: str,
        *,
        now: datetime,
    ) -> LocationScope: ...


class GeographyPolicy:
    def __init__(
        self,
        *,
        min_radius_meters: int = 100,
        max_radius_meters: int = 500_000,
        max_pharmacies: int = 100,
    ) -> None:
        if not 0 < min_radius_meters <= max_radius_meters:
            raise ValueError("radius policy is invalid")
        self._min_radius = min_radius_meters
        self._max_radius = max_radius_meters
        self._max_pharmacies = max_pharmacies

    def normalize(self, raw: LocationScopeInput) -> LocationScopeInput:
        coordinate = self._coordinate(raw.coordinate) if raw.coordinate else None
        value = LocationScopeInput(
            kind=raw.kind,
            country_key=self._key(raw.country_key),
            region_key=self._key(raw.region_key),
            city_key=self._key(raw.city_key),
            district_key=self._key(raw.district_key),
            coordinate=coordinate,
            radius_meters=raw.radius_meters,
            address_key=self._key(raw.address_key),
            pharmacy_ids=tuple(sorted(set(raw.pharmacy_ids))),
            online_region_key=self._key(raw.online_region_key),
        )
        self._validate_shape(value)
        return value

    def fingerprint(self, value: LocationScopeInput) -> str:
        payload = asdict(value)
        payload["kind"] = value.kind.value
        if value.coordinate:
            payload["coordinate"] = {
                "latitude": format(value.coordinate.latitude, "f"),
                "longitude": format(value.coordinate.longitude, "f"),
            }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def decide(
        self,
        scope: LocationScopeInput,
        point: PointLocation,
    ) -> GeographicDecision:
        value = self.normalize(scope)
        if value.kind is LocationScopeKind.RADIUS:
            if point.coordinate is None:
                return GeographicDecision(
                    GeographicEligibility.UNKNOWN,
                    GeographicReason.COORDINATE_MISSING,
                )
            distance = distance_meters(value.coordinate, point.coordinate)  # type: ignore[arg-type]
            return GeographicDecision(
                (
                    GeographicEligibility.ELIGIBLE
                    if distance <= value.radius_meters  # type: ignore[operator]
                    else GeographicEligibility.INELIGIBLE
                ),
                (
                    GeographicReason.WITHIN_RADIUS
                    if distance <= value.radius_meters  # type: ignore[operator]
                    else GeographicReason.OUTSIDE_RADIUS
                ),
                distance,
            )
        if value.kind is LocationScopeKind.PHARMACY_LIST:
            if point.pharmacy_id is None:
                return GeographicDecision(
                    GeographicEligibility.UNKNOWN,
                    GeographicReason.PHARMACY_NOT_SELECTED,
                )
            selected = point.pharmacy_id in value.pharmacy_ids
            return GeographicDecision(
                GeographicEligibility.ELIGIBLE if selected else GeographicEligibility.INELIGIBLE,
                GeographicReason.PHARMACY_SELECTED
                if selected
                else GeographicReason.PHARMACY_NOT_SELECTED,
            )
        if value.kind is LocationScopeKind.ONLINE_REGION:
            if not point.online_region_keys:
                return GeographicDecision(
                    GeographicEligibility.UNKNOWN,
                    GeographicReason.ONLINE_REGION_UNKNOWN,
                )
            selected = value.online_region_key in point.online_region_keys
            return GeographicDecision(
                GeographicEligibility.ELIGIBLE if selected else GeographicEligibility.INELIGIBLE,
                GeographicReason.ONLINE_REGION_SERVED
                if selected
                else GeographicReason.KEY_MISMATCH,
            )
        field = {
            LocationScopeKind.COUNTRY: "country_key",
            LocationScopeKind.REGION: "region_key",
            LocationScopeKind.CITY: "city_key",
            LocationScopeKind.DISTRICT: "district_key",
            LocationScopeKind.ADDRESS: "address_key",
        }[value.kind]
        expected = getattr(value, field)
        actual = getattr(point, field)
        if actual is None:
            return GeographicDecision(
                GeographicEligibility.UNKNOWN,
                GeographicReason.KEY_MISMATCH,
            )
        matches = expected == self._key(actual)
        return GeographicDecision(
            GeographicEligibility.ELIGIBLE if matches else GeographicEligibility.INELIGIBLE,
            GeographicReason.EXACT_KEY if matches else GeographicReason.KEY_MISMATCH,
        )

    def _validate_shape(self, value: LocationScopeInput) -> None:
        required = {
            LocationScopeKind.COUNTRY: ("country_key",),
            LocationScopeKind.REGION: ("region_key",),
            LocationScopeKind.CITY: ("city_key",),
            LocationScopeKind.DISTRICT: ("district_key",),
            LocationScopeKind.RADIUS: ("coordinate", "radius_meters"),
            LocationScopeKind.ADDRESS: ("address_key", "coordinate"),
            LocationScopeKind.PHARMACY_LIST: ("pharmacy_ids",),
            LocationScopeKind.ONLINE_REGION: ("online_region_key",),
        }[value.kind]
        allowed = set(required)
        populated = {
            field
            for field in (
                "country_key",
                "region_key",
                "city_key",
                "district_key",
                "coordinate",
                "radius_meters",
                "address_key",
                "pharmacy_ids",
                "online_region_key",
            )
            if getattr(value, field)
        }
        if not all(getattr(value, field) for field in required) or populated - allowed:
            raise GeographyValidationError("location scope fields do not match its kind")
        if value.kind is LocationScopeKind.RADIUS and not (
            self._min_radius <= (value.radius_meters or 0) <= self._max_radius
        ):
            raise GeographyValidationError("radius is outside configured bounds")
        if value.kind is LocationScopeKind.PHARMACY_LIST and (
            not value.pharmacy_ids
            or len(value.pharmacy_ids) > self._max_pharmacies
            or any(item <= 0 for item in value.pharmacy_ids)
        ):
            raise GeographyValidationError("pharmacy selection is invalid")

    @staticmethod
    def _coordinate(value: Coordinate) -> Coordinate:
        latitude = value.latitude.quantize(Decimal("0.000001"))
        longitude = value.longitude.quantize(Decimal("0.000001"))
        if not Decimal("-90") <= latitude <= Decimal("90"):
            raise GeographyValidationError("latitude is outside valid range")
        if not Decimal("-180") <= longitude <= Decimal("180"):
            raise GeographyValidationError("longitude is outside valid range")
        return Coordinate(latitude, longitude)

    @staticmethod
    def _key(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value).strip().casefold().replace("ё", "е")
        if not _KEY.fullmatch(normalized):
            raise GeographyValidationError("geographic key is invalid")
        return normalized


class LocationScopeService:
    def __init__(self, repository: LocationScopeRepository, policy: GeographyPolicy) -> None:
        self._repository = repository
        self._policy = policy

    async def create_or_get(
        self,
        raw: LocationScopeInput,
        *,
        now: datetime,
    ) -> LocationScope:
        value = self._policy.normalize(raw)
        return await self._repository.create_or_get(
            value,
            self._policy.fingerprint(value),
            now=now,
        )

    async def revise(
        self,
        scope_id: int,
        expected_version: int,
        raw: LocationScopeInput,
        *,
        now: datetime,
    ) -> LocationScope:
        value = self._policy.normalize(raw)
        return await self._repository.revise(
            scope_id,
            expected_version,
            value,
            self._policy.fingerprint(value),
            now=now,
        )


def distance_meters(first: Coordinate, second: Coordinate) -> int:
    lat1, lon1 = math.radians(float(first.latitude)), math.radians(float(first.longitude))
    lat2, lon2 = math.radians(float(second.latitude)), math.radians(float(second.longitude))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    angular = 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    return round(_EARTH_RADIUS_METERS * angular)
