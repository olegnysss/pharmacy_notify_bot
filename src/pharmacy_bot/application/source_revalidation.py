from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pharmacy_bot.application.catalog_normalization import CatalogNormalizer
from pharmacy_bot.domain.product_matching import MatchLevel
from pharmacy_bot.domain.source_revalidation import (
    DriftClass,
    DriftEvidence,
    DriftResult,
    MonitoringEligibility,
    OfferAvailability,
    RevalidationActor,
    RevalidationAuthorizationError,
    RevalidationCommand,
    RevalidationConflict,
    RevalidationState,
    SourceVersionIdentity,
    SubscriptionAggregateState,
)

_CRITICAL_FIELDS = (
    "kind",
    "active_ingredient",
    "manufacturer",
    "form",
    "dosage",
    "concentration",
    "package_count",
    "volume",
    "route",
    "package_variant",
)


class RevalidationRepository(Protocol):
    async def apply(
        self,
        command: RevalidationCommand,
        *,
        now: datetime,
    ) -> RevalidationState: ...

    async def release(
        self,
        source_product_id: int,
        expected_source_version: int,
        actor: RevalidationActor,
        reason: str,
        *,
        now: datetime,
    ) -> RevalidationState: ...

    async def accept_fresh_check(
        self,
        source_product_id: int,
        expected_source_version: int,
        *,
        now: datetime,
    ) -> RevalidationState: ...

    async def delivery_eligible(
        self,
        source_product_id: int,
        observation_source_version: int,
    ) -> bool: ...


class SourceDriftClassifier:
    algorithm_version = "source-drift-v1"

    def __init__(self, normalizer: CatalogNormalizer) -> None:
        self._normalizer = normalizer

    def classify(
        self,
        previous: SourceVersionIdentity,
        current: SourceVersionIdentity,
    ) -> DriftResult:
        if previous.source_product_id != current.source_product_id:
            raise ValueError("source versions belong to different products")
        changed: list[str] = []
        missing: list[str] = []
        critical: list[str] = []
        for field in _CRITICAL_FIELDS:
            old = getattr(previous.attributes, field)
            new = getattr(current.attributes, field)
            if old == new:
                continue
            changed.append(field)
            if old is not None and new is None:
                missing.append(field)
            elif old is not None and new is not None:
                critical.append(field)
        if critical:
            drift_class = DriftClass.CRITICAL
        elif missing:
            drift_class = DriftClass.INCOMPLETE
        elif changed:
            drift_class = DriftClass.COSMETIC
        else:
            display_changed = (
                self._normalizer.normalize_text(previous.raw_name)
                != self._normalizer.normalize_text(current.raw_name)
                or previous.canonical_url != current.canonical_url
            )
            drift_class = DriftClass.COSMETIC if display_changed else DriftClass.NONE
        return DriftResult(
            drift_class,
            DriftEvidence(tuple(changed), tuple(missing), tuple(critical)),
            self.algorithm_version,
        )


class SourceRevalidationService:
    def __init__(self, repository: RevalidationRepository) -> None:
        self._repository = repository

    async def revalidate(
        self,
        previous: SourceVersionIdentity,
        current: SourceVersionIdentity,
        drift: DriftResult,
        match_level: MatchLevel,
        match_algorithm_version: str,
        *,
        match_confirmed: bool = False,
        now: datetime,
    ) -> RevalidationState:
        if previous.source_product_id != current.source_product_id:
            raise RevalidationConflict("source versions belong to different products")
        if current.source_version <= previous.source_version:
            raise RevalidationConflict("source versions are not ordered")
        command = RevalidationCommand(
            current.source_product_id,
            previous.source_version,
            current.source_version,
            current.observed_at,
            drift,
            match_level,
            match_confirmed,
            match_algorithm_version,
        )
        return await self._repository.apply(command, now=now)

    async def release(
        self,
        source_product_id: int,
        expected_source_version: int,
        actor: RevalidationActor,
        *,
        exact_or_confirmed: bool,
        reason: str,
        now: datetime,
    ) -> RevalidationState:
        if "catalog_mapping" not in actor.roles:
            raise RevalidationAuthorizationError("actor cannot release quarantined offers")
        if not exact_or_confirmed:
            raise RevalidationAuthorizationError("release requires exact or confirmed mapping")
        if not reason or len(reason) > 128:
            raise RevalidationAuthorizationError("release reason is invalid")
        return await self._repository.release(
            source_product_id,
            expected_source_version,
            actor,
            reason,
            now=now,
        )

    async def accept_fresh_check(
        self,
        source_product_id: int,
        expected_source_version: int,
        *,
        exact_or_confirmed: bool,
        now: datetime,
    ) -> RevalidationState:
        if not exact_or_confirmed:
            raise RevalidationAuthorizationError("fresh check requires exact or confirmed mapping")
        return await self._repository.accept_fresh_check(
            source_product_id,
            expected_source_version,
            now=now,
        )

    async def delivery_eligible(
        self,
        source_product_id: int,
        observation_source_version: int,
    ) -> bool:
        return await self._repository.delivery_eligible(
            source_product_id,
            observation_source_version,
        )


def aggregate_subscription_state(
    offers: tuple[OfferAvailability, ...],
) -> SubscriptionAggregateState:
    eligible = tuple(
        offer for offer in offers if offer.eligibility is MonitoringEligibility.ELIGIBLE
    )
    if any(offer.available is True for offer in eligible):
        return SubscriptionAggregateState.AVAILABLE
    if eligible and all(offer.available is False for offer in eligible):
        return SubscriptionAggregateState.UNAVAILABLE
    if offers and not eligible:
        return SubscriptionAggregateState.REQUIRES_CLARIFICATION
    return SubscriptionAggregateState.UNKNOWN
