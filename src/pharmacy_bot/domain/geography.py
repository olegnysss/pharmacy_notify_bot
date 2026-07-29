from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class LocationScopeKind(StrEnum):
    COUNTRY = "country"
    REGION = "region"
    CITY = "city"
    DISTRICT = "district"
    RADIUS = "radius"
    ADDRESS = "address"
    PHARMACY_LIST = "pharmacy_list"
    ONLINE_REGION = "online_region"


@dataclass(frozen=True, slots=True)
class Coordinate:
    latitude: Decimal
    longitude: Decimal


@dataclass(frozen=True, slots=True)
class LocationScopeInput:
    kind: LocationScopeKind
    country_key: str | None = None
    region_key: str | None = None
    city_key: str | None = None
    district_key: str | None = None
    coordinate: Coordinate | None = None
    radius_meters: int | None = None
    address_key: str | None = None
    pharmacy_ids: tuple[int, ...] = ()
    online_region_key: str | None = None


@dataclass(frozen=True, slots=True)
class LocationScope:
    id: int
    version: int
    value: LocationScopeInput
    fingerprint: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PointLocation:
    coordinate: Coordinate | None = None
    country_key: str | None = None
    region_key: str | None = None
    city_key: str | None = None
    district_key: str | None = None
    address_key: str | None = None
    pharmacy_id: int | None = None
    online_region_keys: frozenset[str] = frozenset()


class GeographicEligibility(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class GeographicReason(StrEnum):
    EXACT_KEY = "exact_key"
    KEY_MISMATCH = "key_mismatch"
    WITHIN_RADIUS = "within_radius"
    OUTSIDE_RADIUS = "outside_radius"
    COORDINATE_MISSING = "coordinate_missing"
    PHARMACY_SELECTED = "pharmacy_selected"
    PHARMACY_NOT_SELECTED = "pharmacy_not_selected"
    ONLINE_REGION_SERVED = "online_region_served"
    ONLINE_REGION_UNKNOWN = "online_region_unknown"


@dataclass(frozen=True, slots=True)
class GeographicDecision:
    eligibility: GeographicEligibility
    reason: GeographicReason
    distance_meters: int | None = None


class LocationScopeConflict(Exception):
    pass


class StaleLocationScopeVersion(Exception):
    pass
