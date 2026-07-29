from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pharmacy_bot.domain.geography import Coordinate


class PharmacyKind(StrEnum):
    PHARMACY = "pharmacy"
    PICKUP_POINT = "pickup_point"


class PharmacyStatus(StrEnum):
    ACTIVE = "active"
    TEMPORARILY_CLOSED = "temporarily_closed"
    RETIRED = "retired"


class PharmacyMatchLevel(StrEnum):
    EXACT = "exact"
    PROBABLE = "probable"
    CANDIDATE = "candidate"
    MISMATCH = "mismatch"


@dataclass(frozen=True, slots=True)
class PharmacyIdentity:
    name: str
    normalized_address: str
    network_key: str | None
    coordinate: Coordinate | None
    kind: PharmacyKind = PharmacyKind.PHARMACY
    trusted_identifier: str | None = None


@dataclass(frozen=True, slots=True)
class Pharmacy:
    id: int
    version: int
    identity: PharmacyIdentity
    status: PharmacyStatus
    fingerprint: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourcePharmacy:
    id: int
    source_code: str
    external_id: str
    identity: PharmacyIdentity
    status: PharmacyStatus
    canonical_pharmacy_id: int | None
    mapping_level: PharmacyMatchLevel | None
    mapping_version: int
    version: int
    fingerprint: str
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class PharmacyMatchResult:
    level: PharmacyMatchLevel
    score: int
    reasons: tuple[str, ...]
    algorithm_version: str = "pharmacy-match-v1"


@dataclass(frozen=True, slots=True)
class PharmacyPageItem:
    pharmacy: Pharmacy
    distance_meters: int


@dataclass(frozen=True, slots=True)
class PharmacyPage:
    items: tuple[PharmacyPageItem, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class PharmacyMappingActor:
    internal_id: int
    roles: frozenset[str]


class PharmacyDirectoryConflict(Exception):
    pass


class StalePharmacyMapping(Exception):
    pass
