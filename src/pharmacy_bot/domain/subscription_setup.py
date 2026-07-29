from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SetupStatus(StrEnum):
    CHOOSE_LOCATION = "choose_location"
    AWAITING_LOCATION = "awaiting_location"
    CONFIRM_LOCATION = "confirm_location"
    CHOOSE_RADIUS = "choose_radius"
    CHOOSE_SOURCES = "choose_sources"
    CHOOSE_FILTERS = "choose_filters"
    CHOOSE_COMPLETION = "choose_completion"
    AWAITING_END_DATE = "awaiting_end_date"
    REVIEW = "review"
    CREATED = "created"
    CANCELLED = "cancelled"


class LocationInputMode(StrEnum):
    CITY = "city"
    ADDRESS = "address"
    COORDINATES = "coordinates"


class LocationConfidence(StrEnum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"


class CompletionMode(StrEnum):
    CONTINUE = "continue"
    PAUSE_AFTER_SUCCESS = "pause_after_success"
    COMPLETE_AFTER_SUCCESS = "complete_after_success"
    UNTIL_DATE = "until_date"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    DELETED = "deleted"


class AvailabilityState(StrEnum):
    PENDING = "pending"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"
    LOW_STOCK = "low_stock"
    ORDERABLE = "orderable"
    SOURCE_ERROR = "source_error"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    candidate_key: str
    version: str
    name: str
    form: str | None
    dosage: str | None
    package: str | None
    manufacturer: str | None
    source_host: str | None


@dataclass(frozen=True, slots=True)
class LocationCandidate:
    key: str
    kind: LocationInputMode
    display_name: str
    city: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    confidence: LocationConfidence = LocationConfidence.AMBIGUOUS
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class SourceOption:
    code: str
    name: str
    available: bool
    unavailable_reason: str | None = None
    supports_price: bool = False
    supports_low_stock: bool = False
    supports_orderable: bool = False
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class MonitoringFilters:
    notify_low_stock: bool = False
    notify_orderable: bool = False
    include_price: bool = False


@dataclass(frozen=True, slots=True)
class SubscriptionSetupDraft:
    id: int
    user_id: int
    generation: int
    status: SetupStatus
    product: ProductSnapshot
    location_mode: LocationInputMode | None
    location_candidates: tuple[LocationCandidate, ...]
    location: LocationCandidate | None
    radius_meters: int | None
    available_sources: tuple[SourceOption, ...]
    selected_source_codes: tuple[str, ...]
    filters: MonitoringFilters
    completion_mode: CompletionMode | None
    ends_at: datetime | None
    idempotency_key: str
    expires_at: datetime
    subscription_id: int | None = None


@dataclass(frozen=True, slots=True)
class Subscription:
    id: int
    user_id: int
    product: ProductSnapshot
    location: LocationCandidate
    radius_meters: int
    source_codes: tuple[str, ...]
    filters: MonitoringFilters
    completion_mode: CompletionMode
    ends_at: datetime | None
    status: SubscriptionStatus
    availability_state: AvailabilityState
    created_at: datetime
