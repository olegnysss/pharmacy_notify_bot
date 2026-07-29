from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MatchLevel(StrEnum):
    EXACT = "exact"
    PROBABLE = "probable"
    CANDIDATE = "candidate"
    MISMATCH = "mismatch"


class MatchReason(StrEnum):
    TRUSTED_IDENTIFIER = "trusted_identifier"
    CRITICAL_SIGNATURE = "critical_signature"
    SCORED_FEATURES = "scored_features"
    KIND_MISMATCH = "kind_mismatch"
    TRADE_NAME_MISMATCH = "trade_name_mismatch"
    ACTIVE_INGREDIENT_MISMATCH = "active_ingredient_mismatch"
    MANUFACTURER_MISMATCH = "manufacturer_mismatch"
    FORM_MISMATCH = "form_mismatch"
    DOSAGE_MISMATCH = "dosage_mismatch"
    CONCENTRATION_MISMATCH = "concentration_mismatch"
    PACKAGE_COUNT_MISMATCH = "package_count_mismatch"
    VOLUME_MISMATCH = "volume_mismatch"
    ROUTE_MISMATCH = "route_mismatch"
    PACKAGE_VARIANT_MISMATCH = "package_variant_mismatch"
    INSUFFICIENT_SIMILARITY = "insufficient_similarity"


@dataclass(frozen=True, slots=True)
class MatchIdentity:
    kind: str
    trade_name: str
    active_ingredient: str | None = None
    manufacturer: str | None = None
    form: str | None = None
    dosage: str | None = None
    concentration: str | None = None
    package_count: int | None = None
    volume: str | None = None
    route: str | None = None
    package_variant: str | None = None
    trusted_identifiers: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True, slots=True)
class MatchRequest:
    source_product_id: int
    source_product_version: int
    source_code: str
    source: MatchIdentity
    canonical_product_id: int
    canonical_product_version: int
    canonical: MatchIdentity


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    matched_features: tuple[str, ...]
    missing_features: tuple[str, ...]
    mismatched_features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MatchResult:
    level: MatchLevel
    score: int
    reasons: tuple[MatchReason, ...]
    evidence: MatchEvidence
    algorithm_version: str
    distinguishing_features: tuple[str, ...]
    auto_event_allowed: bool


class MappingActorType(StrEnum):
    USER = "user"
    OPERATOR = "operator"


class MappingScope(StrEnum):
    USER = "user"
    SOURCE = "source"
    GLOBAL = "global"


class MappingDecisionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class MappingDecision:
    id: int
    source_product_id: int
    canonical_product_id: int
    canonical_product_version: int
    actor_type: MappingActorType
    actor_internal_id: int
    scope: MappingScope
    scope_user_id: int | None
    source_code: str
    reason_code: str
    algorithm_version: str
    idempotency_key: str
    status: MappingDecisionStatus
    version: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class MappingAuthorizationError(Exception):
    pass


class MappingConflict(Exception):
    pass


class StaleMappingDecision(Exception):
    pass
