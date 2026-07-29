from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    LocationCandidate,
    LocationInputMode,
    MonitoringFilters,
    SourceOption,
    Subscription,
)


class EditStatus(StrEnum):
    CHOOSE_BLOCK = "choose_block"
    AWAITING_LOCATION = "awaiting_location"
    CONFIRM_LOCATION = "confirm_location"
    CHOOSE_RADIUS = "choose_radius"
    CHOOSE_SOURCES = "choose_sources"
    CHOOSE_FILTERS = "choose_filters"
    CHOOSE_COMPLETION = "choose_completion"
    AWAITING_END_DATE = "awaiting_end_date"
    REVIEW = "review"
    APPLIED = "applied"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SubscriptionEditDraft:
    id: int
    user_id: int
    subscription_id: int
    generation: int
    status: EditStatus
    base_updated_at: datetime
    original: Subscription
    location_mode: LocationInputMode | None
    location_candidates: tuple[LocationCandidate, ...]
    location: LocationCandidate
    radius_meters: int
    available_sources: tuple[SourceOption, ...]
    selected_source_codes: tuple[str, ...]
    filters: MonitoringFilters
    completion_mode: CompletionMode
    ends_at: datetime | None
    idempotency_key: str
    expires_at: datetime
    applied_subscription: Subscription | None = None


class LifecycleAction(StrEnum):
    EDITED = "edited"
    PAUSED = "paused"
    RESUMED = "resumed"
    DELETED = "deleted"
    ALREADY_APPLIED = "already_applied"
    INVALID_STATE = "invalid_state"
    CONFIGURATION_INVALID = "configuration_invalid"
    NOT_FOUND = "not_found"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    action: LifecycleAction
    subscription: Subscription | None = None
