from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.source_revalidation import RevalidationRepository
from pharmacy_bot.domain.product_matching import MatchLevel
from pharmacy_bot.domain.source_revalidation import (
    DriftClass,
    MonitoringEligibility,
    RevalidationAction,
    RevalidationActor,
    RevalidationCommand,
    RevalidationConflict,
    RevalidationState,
)
from pharmacy_bot.infrastructure.models import (
    SourceProductModel,
    SourceProductRevalidationModel,
)


class SqlAlchemySourceRevalidationRepository(RevalidationRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def apply(
        self,
        command: RevalidationCommand,
        *,
        now: datetime,
    ) -> RevalidationState:
        async with self._session_factory.begin() as session:
            model = await self._locked(session, command.source_product_id)
            if model is None:
                raise RevalidationConflict("source product does not exist")
            existing = await session.scalar(
                select(SourceProductRevalidationModel).where(
                    SourceProductRevalidationModel.source_product_id == command.source_product_id,
                    SourceProductRevalidationModel.source_version == command.source_version,
                    SourceProductRevalidationModel.algorithm_version
                    == command.drift.algorithm_version,
                )
            )
            if existing is not None:
                return self._state(model, RevalidationAction(existing.action))
            if (
                command.source_version != model.version
                or command.source_version <= model.last_revalidated_version
            ):
                action = RevalidationAction.STALE_IGNORED
            else:
                reason = self._quarantine_reason(command)
                if reason is None:
                    model.monitoring_eligibility = MonitoringEligibility.ELIGIBLE.value
                    model.quarantine_reason = None
                    model.quarantined_at = None
                    model.fresh_check_required = False
                    action = RevalidationAction.VALIDATED
                else:
                    model.monitoring_eligibility = MonitoringEligibility.QUARANTINED.value
                    model.quarantine_reason = reason
                    model.quarantined_at = now
                    model.fresh_check_required = False
                    action = RevalidationAction.QUARANTINED
                model.last_revalidated_version = command.source_version
                model.last_revalidated_at = now
            session.add(
                SourceProductRevalidationModel(
                    source_product_id=model.id,
                    previous_version=command.previous_version,
                    source_version=command.source_version,
                    drift_class=command.drift.drift_class.value,
                    match_level=command.match_level.value,
                    match_confirmed=command.match_confirmed,
                    algorithm_version=command.drift.algorithm_version,
                    match_algorithm_version=command.match_algorithm_version,
                    action=action.value,
                    safe_evidence={
                        "changed_fields": list(command.drift.evidence.changed_fields),
                        "missing_fields": list(command.drift.evidence.missing_fields),
                        "critical_fields": list(command.drift.evidence.critical_fields),
                    },
                    observed_at=command.observed_at,
                    created_at=now,
                )
            )
            await session.flush()
            return self._state(model, action)

    async def release(
        self,
        source_product_id: int,
        expected_source_version: int,
        actor: RevalidationActor,
        reason: str,
        *,
        now: datetime,
    ) -> RevalidationState:
        async with self._session_factory.begin() as session:
            model = await self._locked(session, source_product_id)
            if model is None:
                raise RevalidationConflict("source product does not exist")
            if model.version != expected_source_version:
                raise RevalidationConflict("source product version changed")
            if model.monitoring_eligibility == MonitoringEligibility.AWAITING_FRESH_CHECK.value:
                return self._state(model, RevalidationAction.RELEASED)
            if model.monitoring_eligibility != MonitoringEligibility.QUARANTINED.value:
                raise RevalidationConflict("source product is not quarantined")
            model.monitoring_eligibility = MonitoringEligibility.AWAITING_FRESH_CHECK.value
            model.quarantine_reason = None
            model.fresh_check_required = True
            await self._audit_transition(
                session,
                model,
                "manual-release-v1",
                RevalidationAction.RELEASED,
                now,
                actor=actor,
                reason=reason,
            )
            return self._state(model, RevalidationAction.RELEASED)

    async def accept_fresh_check(
        self,
        source_product_id: int,
        expected_source_version: int,
        *,
        now: datetime,
    ) -> RevalidationState:
        async with self._session_factory.begin() as session:
            model = await self._locked(session, source_product_id)
            if model is None or model.version != expected_source_version:
                raise RevalidationConflict("source product version changed or disappeared")
            if model.monitoring_eligibility == MonitoringEligibility.ELIGIBLE.value:
                return self._state(model, RevalidationAction.FRESH_CHECK_ACCEPTED)
            if (
                model.monitoring_eligibility != MonitoringEligibility.AWAITING_FRESH_CHECK.value
                or not model.fresh_check_required
            ):
                raise RevalidationConflict("source product does not await a fresh check")
            model.monitoring_eligibility = MonitoringEligibility.ELIGIBLE.value
            model.fresh_check_required = False
            await self._audit_transition(
                session,
                model,
                "fresh-check-v1",
                RevalidationAction.FRESH_CHECK_ACCEPTED,
                now,
            )
            return self._state(model, RevalidationAction.FRESH_CHECK_ACCEPTED)

    async def delivery_eligible(
        self,
        source_product_id: int,
        observation_source_version: int,
    ) -> bool:
        async with self._session_factory() as session:
            model = await session.get(SourceProductModel, source_product_id)
            return bool(
                model
                and model.version == observation_source_version
                and model.last_revalidated_version >= model.version
                and model.monitoring_eligibility == MonitoringEligibility.ELIGIBLE.value
                and not model.fresh_check_required
            )

    @staticmethod
    def _quarantine_reason(command: RevalidationCommand) -> str | None:
        if command.drift.drift_class is DriftClass.CRITICAL:
            return "critical_drift"
        if command.drift.drift_class is DriftClass.INCOMPLETE:
            return "incomplete_identity"
        if command.match_level is MatchLevel.MISMATCH:
            return "mapping_mismatch"
        if command.match_level is not MatchLevel.EXACT and not command.match_confirmed:
            return "mapping_requires_confirmation"
        return None

    @staticmethod
    async def _audit_transition(
        session: AsyncSession,
        model: SourceProductModel,
        algorithm_version: str,
        action: RevalidationAction,
        now: datetime,
        *,
        actor: RevalidationActor | None = None,
        reason: str | None = None,
    ) -> None:
        await session.execute(
            insert(SourceProductRevalidationModel)
            .values(
                source_product_id=model.id,
                previous_version=model.version,
                source_version=model.version,
                drift_class=DriftClass.NONE.value,
                match_level=MatchLevel.EXACT.value,
                match_confirmed=True,
                algorithm_version=algorithm_version,
                match_algorithm_version="confirmed-mapping",
                action=action.value,
                safe_evidence={},
                actor_type=actor.actor_type if actor else None,
                actor_internal_id=actor.internal_id if actor else None,
                reason_code=reason,
                observed_at=now,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    SourceProductRevalidationModel.source_product_id,
                    SourceProductRevalidationModel.source_version,
                    SourceProductRevalidationModel.algorithm_version,
                ]
            )
        )

    @staticmethod
    def _state(
        model: SourceProductModel,
        action: RevalidationAction,
    ) -> RevalidationState:
        return RevalidationState(
            source_product_id=model.id,
            source_version=model.version,
            last_revalidated_version=model.last_revalidated_version,
            eligibility=MonitoringEligibility(model.monitoring_eligibility),
            quarantine_reason=model.quarantine_reason,
            fresh_check_required=model.fresh_check_required,
            action=action,
        )

    @staticmethod
    async def _locked(
        session: AsyncSession,
        source_product_id: int,
    ) -> SourceProductModel | None:
        return cast(
            SourceProductModel | None,
            await session.scalar(
                select(SourceProductModel)
                .where(SourceProductModel.id == source_product_id)
                .with_for_update()
            ),
        )
