from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pharmacy_bot.domain.product_matching import MatchLevel
from pharmacy_bot.domain.source_product import SourceProductAttributes


class DriftClass(StrEnum):
    NONE = "none"
    COSMETIC = "cosmetic"
    INCOMPLETE = "incomplete"
    CRITICAL = "critical"


class MonitoringEligibility(StrEnum):
    PENDING_REVALIDATION = "pending_revalidation"
    ELIGIBLE = "eligible"
    QUARANTINED = "quarantined"
    AWAITING_FRESH_CHECK = "awaiting_fresh_check"


class RevalidationAction(StrEnum):
    UNCHANGED = "unchanged"
    VALIDATED = "validated"
    QUARANTINED = "quarantined"
    RELEASED = "released"
    FRESH_CHECK_ACCEPTED = "fresh_check_accepted"
    STALE_IGNORED = "stale_ignored"


@dataclass(frozen=True, slots=True)
class SourceVersionIdentity:
    source_product_id: int
    source_version: int
    observed_at: datetime
    canonical_url: str
    raw_name: str
    attributes: SourceProductAttributes
    semantic_fingerprint: str


@dataclass(frozen=True, slots=True)
class DriftEvidence:
    changed_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    critical_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DriftResult:
    drift_class: DriftClass
    evidence: DriftEvidence
    algorithm_version: str


@dataclass(frozen=True, slots=True)
class RevalidationCommand:
    source_product_id: int
    previous_version: int
    source_version: int
    observed_at: datetime
    drift: DriftResult
    match_level: MatchLevel
    match_confirmed: bool
    match_algorithm_version: str


@dataclass(frozen=True, slots=True)
class RevalidationState:
    source_product_id: int
    source_version: int
    last_revalidated_version: int
    eligibility: MonitoringEligibility
    quarantine_reason: str | None
    fresh_check_required: bool
    action: RevalidationAction


@dataclass(frozen=True, slots=True)
class RevalidationActor:
    actor_type: str
    internal_id: int
    roles: frozenset[str] = frozenset()


class SubscriptionAggregateState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    REQUIRES_CLARIFICATION = "requires_clarification"


@dataclass(frozen=True, slots=True)
class OfferAvailability:
    source_product_id: int
    eligibility: MonitoringEligibility
    available: bool | None


class RevalidationAuthorizationError(Exception):
    pass


class RevalidationConflict(Exception):
    pass
