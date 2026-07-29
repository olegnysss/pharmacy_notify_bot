from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pharmacy_bot.domain.product_matching import (
    MappingActorType,
    MappingAuthorizationError,
    MappingDecision,
    MappingScope,
    MatchEvidence,
    MatchLevel,
    MatchReason,
    MatchRequest,
    MatchResult,
)

_CRITICAL_REASONS = {
    "kind": MatchReason.KIND_MISMATCH,
    "active_ingredient": MatchReason.ACTIVE_INGREDIENT_MISMATCH,
    "manufacturer": MatchReason.MANUFACTURER_MISMATCH,
    "form": MatchReason.FORM_MISMATCH,
    "dosage": MatchReason.DOSAGE_MISMATCH,
    "concentration": MatchReason.CONCENTRATION_MISMATCH,
    "package_count": MatchReason.PACKAGE_COUNT_MISMATCH,
    "volume": MatchReason.VOLUME_MISMATCH,
    "route": MatchReason.ROUTE_MISMATCH,
    "package_variant": MatchReason.PACKAGE_VARIANT_MISMATCH,
}
_WEIGHTS = {
    "kind": 5,
    "trade_name": 25,
    "active_ingredient": 15,
    "manufacturer": 10,
    "form": 10,
    "dosage": 10,
    "concentration": 10,
    "package_count": 5,
    "volume": 3,
    "route": 4,
    "package_variant": 3,
}


@dataclass(frozen=True, slots=True)
class MatchingThresholds:
    probable_score: int = 70
    candidate_score: int = 30

    def __post_init__(self) -> None:
        if not 0 <= self.candidate_score < self.probable_score < 100:
            raise ValueError("matching thresholds must satisfy 0 <= candidate < probable < 100")


@dataclass(frozen=True, slots=True)
class MappingActor:
    actor_type: MappingActorType
    internal_id: int
    roles: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class MappingConfirmation:
    request: MatchRequest
    result: MatchResult
    actor: MappingActor
    scope: MappingScope
    scope_user_id: int | None
    reason_code: str
    idempotency_key: str


class MappingDecisionRepository(Protocol):
    async def create_or_get(
        self,
        confirmation: MappingConfirmation,
        *,
        now: datetime,
    ) -> MappingDecision: ...

    async def revoke(
        self,
        decision_id: int,
        expected_version: int,
        actor: MappingActor,
        *,
        now: datetime,
    ) -> MappingDecision: ...

    async def active_rule_exists(
        self,
        source_product_id: int,
        canonical_product_id: int,
        *,
        source_code: str,
        user_id: int | None,
    ) -> bool: ...


class ProductMatchingEngine:
    algorithm_version = "critical-gate-v1"

    def __init__(self, thresholds: MatchingThresholds | None = None) -> None:
        self._thresholds = thresholds or MatchingThresholds()

    def evaluate(self, request: MatchRequest) -> MatchResult:
        matched: list[str] = []
        missing: list[str] = []
        mismatched: list[str] = []
        reasons: list[MatchReason] = []
        weighted_matched = 0
        weighted_available = 0
        for feature, weight in _WEIGHTS.items():
            source = getattr(request.source, feature)
            canonical = getattr(request.canonical, feature)
            if source is None or canonical is None:
                missing.append(feature)
                continue
            weighted_available += weight
            if source == canonical:
                matched.append(feature)
                weighted_matched += weight
            else:
                mismatched.append(feature)
                reason = _CRITICAL_REASONS.get(feature)
                if reason is not None:
                    reasons.append(reason)

        evidence = MatchEvidence(
            matched_features=tuple(matched),
            missing_features=tuple(missing),
            mismatched_features=tuple(mismatched),
        )
        distinguishing = self._distinguishing(request)
        if reasons:
            return MatchResult(
                MatchLevel.MISMATCH,
                0,
                tuple(reasons),
                evidence,
                self.algorithm_version,
                distinguishing,
                False,
            )

        common_identifier = (
            request.source.trusted_identifiers & request.canonical.trusted_identifiers
        )
        if common_identifier:
            return MatchResult(
                MatchLevel.EXACT,
                100,
                (MatchReason.TRUSTED_IDENTIFIER,),
                evidence,
                self.algorithm_version,
                distinguishing,
                True,
            )
        full_critical_signature = (
            request.source.kind == request.canonical.kind
            and request.source.trade_name == request.canonical.trade_name
            and request.source.manufacturer is not None
            and request.source.manufacturer == request.canonical.manufacturer
            and request.source.form is not None
            and request.source.form == request.canonical.form
            and (
                (
                    request.source.dosage is not None
                    and request.source.dosage == request.canonical.dosage
                )
                or (
                    request.source.concentration is not None
                    and request.source.concentration == request.canonical.concentration
                )
            )
            and all(
                getattr(request.source, feature) == getattr(request.canonical, feature)
                for feature in _CRITICAL_REASONS
            )
        )
        if full_critical_signature:
            return MatchResult(
                MatchLevel.EXACT,
                100,
                (MatchReason.CRITICAL_SIGNATURE,),
                evidence,
                self.algorithm_version,
                distinguishing,
                True,
            )
        score = round(weighted_matched * 100 / sum(_WEIGHTS.values())) if weighted_available else 0
        if score < self._thresholds.candidate_score:
            level = MatchLevel.MISMATCH
            score_reasons = (MatchReason.INSUFFICIENT_SIMILARITY,)
        elif score >= self._thresholds.probable_score:
            level = MatchLevel.PROBABLE
            score_reasons = (MatchReason.SCORED_FEATURES,)
        else:
            level = MatchLevel.CANDIDATE
            score_reasons = (MatchReason.SCORED_FEATURES,)
        return MatchResult(
            level,
            score,
            score_reasons,
            evidence,
            self.algorithm_version,
            distinguishing,
            False,
        )

    @staticmethod
    def rank(results: tuple[tuple[int, MatchResult], ...]) -> tuple[tuple[int, MatchResult], ...]:
        priority = {
            MatchLevel.EXACT: 0,
            MatchLevel.PROBABLE: 1,
            MatchLevel.CANDIDATE: 2,
            MatchLevel.MISMATCH: 3,
        }
        return tuple(
            sorted(
                results,
                key=lambda item: (priority[item[1].level], -item[1].score, item[0]),
            )
        )

    @staticmethod
    def _distinguishing(request: MatchRequest) -> tuple[str, ...]:
        values = request.canonical
        return tuple(
            f"{label}: {value}"
            for label, value in (
                ("form", values.form),
                ("dosage", values.dosage or values.concentration),
                ("package", values.package_count or values.package_variant or values.volume),
                ("manufacturer", values.manufacturer),
            )
            if value is not None
        )


class MappingDecisionService:
    def __init__(self, repository: MappingDecisionRepository) -> None:
        self._repository = repository

    async def confirm(
        self,
        confirmation: MappingConfirmation,
        *,
        now: datetime,
    ) -> MappingDecision:
        self._authorize(confirmation)
        if confirmation.result.level not in {MatchLevel.PROBABLE, MatchLevel.CANDIDATE}:
            raise MappingAuthorizationError(
                "only non-conflicting uncertain matches are confirmable"
            )
        if not confirmation.reason_code or len(confirmation.reason_code) > 128:
            raise MappingAuthorizationError("mapping reason is invalid")
        if not 8 <= len(confirmation.idempotency_key) <= 128:
            raise MappingAuthorizationError("idempotency key is invalid")
        return await self._repository.create_or_get(confirmation, now=now)

    async def revoke(
        self,
        decision_id: int,
        expected_version: int,
        actor: MappingActor,
        *,
        now: datetime,
    ) -> MappingDecision:
        if actor.actor_type is MappingActorType.OPERATOR and "catalog_mapping" not in actor.roles:
            raise MappingAuthorizationError("operator role does not allow mapping changes")
        return await self._repository.revoke(
            decision_id,
            expected_version,
            actor,
            now=now,
        )

    async def auto_event_allowed(
        self,
        request: MatchRequest,
        result: MatchResult,
        *,
        user_id: int | None,
    ) -> bool:
        if result.level is MatchLevel.EXACT:
            return True
        if result.level is MatchLevel.MISMATCH:
            return False
        return await self._repository.active_rule_exists(
            request.source_product_id,
            request.canonical_product_id,
            source_code=request.source_code,
            user_id=user_id,
        )

    @staticmethod
    def _authorize(confirmation: MappingConfirmation) -> None:
        actor = confirmation.actor
        if actor.actor_type is MappingActorType.USER:
            if (
                confirmation.scope is not MappingScope.USER
                or confirmation.scope_user_id != actor.internal_id
            ):
                raise MappingAuthorizationError("user mappings must be limited to that user")
        elif "catalog_mapping" not in actor.roles:
            raise MappingAuthorizationError("operator role does not allow mapping changes")
        if confirmation.scope is MappingScope.USER and confirmation.scope_user_id is None:
            raise MappingAuthorizationError("user scope requires an internal user ID")
