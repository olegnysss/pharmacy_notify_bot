from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pharmacy_bot.domain.geography import Coordinate


class GeocodingPrecision(StrEnum):
    ROOFTOP = "rooftop"
    ADDRESS = "address"
    STREET = "street"
    CITY = "city"
    REGION = "region"
    UNKNOWN = "unknown"


class GeocodingDecision(StrEnum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT = "insufficient"
    TEMPORARY_ERROR = "temporary_error"


@dataclass(frozen=True, slots=True)
class GeocodingCandidate:
    candidate_id: str
    provider_code: str
    external_id: str
    normalized_address: str
    coordinate: Coordinate
    precision: GeocodingPrecision


@dataclass(frozen=True, slots=True)
class GeocodingResult:
    decision: GeocodingDecision
    generation: int
    candidates: tuple[GeocodingCandidate, ...]
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class ConfirmedGeocoding:
    session_id: int
    user_id: int
    generation: int
    candidate: GeocodingCandidate
    provider_data_version: str
    confirmed_at: datetime


class GeocodingValidationError(Exception):
    pass


class GeocodingConflict(Exception):
    pass
