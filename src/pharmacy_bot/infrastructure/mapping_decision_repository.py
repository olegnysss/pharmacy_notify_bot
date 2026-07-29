from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.application.product_matching import (
    MappingActor,
    MappingConfirmation,
)
from pharmacy_bot.domain.product_matching import (
    MappingActorType,
    MappingAuthorizationError,
    MappingConflict,
    MappingDecision,
    MappingDecisionStatus,
    MappingScope,
    StaleMappingDecision,
)
from pharmacy_bot.infrastructure.models import MappingDecisionModel


class SqlAlchemyMappingDecisionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_or_get(
        self,
        confirmation: MappingConfirmation,
        *,
        now: datetime,
    ) -> MappingDecision:
        actor = confirmation.actor
        request = confirmation.request
        async with self._session_factory.begin() as session:
            decision_id = await session.scalar(
                insert(MappingDecisionModel)
                .values(
                    source_product_id=request.source_product_id,
                    canonical_product_id=request.canonical_product_id,
                    canonical_product_version=request.canonical_product_version,
                    actor_type=actor.actor_type.value,
                    actor_internal_id=actor.internal_id,
                    scope=confirmation.scope.value,
                    scope_user_id=confirmation.scope_user_id,
                    source_code=request.source_code,
                    reason_code=confirmation.reason_code,
                    algorithm_version=confirmation.result.algorithm_version,
                    idempotency_key=confirmation.idempotency_key,
                    status=MappingDecisionStatus.ACTIVE.value,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        MappingDecisionModel.actor_type,
                        MappingDecisionModel.actor_internal_id,
                        MappingDecisionModel.idempotency_key,
                    ]
                )
                .returning(MappingDecisionModel.id)
            )
            if decision_id is None:
                model = await session.scalar(
                    select(MappingDecisionModel).where(
                        MappingDecisionModel.actor_type == actor.actor_type.value,
                        MappingDecisionModel.actor_internal_id == actor.internal_id,
                        MappingDecisionModel.idempotency_key == confirmation.idempotency_key,
                    )
                )
                if model is None:
                    raise RuntimeError("mapping decision was not created or found")
                if not self._same_confirmation(model, confirmation):
                    raise MappingConflict("idempotency key belongs to another mapping decision")
            else:
                model = await session.get(MappingDecisionModel, decision_id)
                if model is None:
                    raise RuntimeError("mapping decision disappeared")
            return self._snapshot(model)

    async def revoke(
        self,
        decision_id: int,
        expected_version: int,
        actor: MappingActor,
        *,
        now: datetime,
    ) -> MappingDecision:
        async with self._session_factory.begin() as session:
            model = cast(
                MappingDecisionModel | None,
                await session.scalar(
                    select(MappingDecisionModel)
                    .where(MappingDecisionModel.id == decision_id)
                    .with_for_update()
                ),
            )
            if model is None:
                raise MappingConflict("mapping decision does not exist")
            if actor.actor_type is MappingActorType.USER and (
                model.actor_type != MappingActorType.USER.value
                or model.actor_internal_id != actor.internal_id
            ):
                raise MappingAuthorizationError("mapping decision belongs to another actor")
            if model.status == MappingDecisionStatus.REVOKED.value:
                if model.version == expected_version + 1:
                    return self._snapshot(model)
                raise StaleMappingDecision
            if model.version != expected_version:
                raise StaleMappingDecision
            model.status = MappingDecisionStatus.REVOKED.value
            model.version += 1
            model.revoked_at = now
            model.updated_at = now
            await session.flush()
            return self._snapshot(model)

    async def active_rule_exists(
        self,
        source_product_id: int,
        canonical_product_id: int,
        *,
        source_code: str,
        user_id: int | None,
    ) -> bool:
        scopes = [
            MappingDecisionModel.scope == MappingScope.GLOBAL.value,
            and_(
                MappingDecisionModel.scope == MappingScope.SOURCE.value,
                MappingDecisionModel.source_code == source_code,
            ),
        ]
        if user_id is not None:
            scopes.append(
                and_(
                    MappingDecisionModel.scope == MappingScope.USER.value,
                    MappingDecisionModel.scope_user_id == user_id,
                )
            )
        async with self._session_factory() as session:
            decision_id = await session.scalar(
                select(MappingDecisionModel.id)
                .where(
                    MappingDecisionModel.source_product_id == source_product_id,
                    MappingDecisionModel.canonical_product_id == canonical_product_id,
                    MappingDecisionModel.status == MappingDecisionStatus.ACTIVE.value,
                    or_(*scopes),
                )
                .limit(1)
            )
            return decision_id is not None

    @staticmethod
    def _same_confirmation(
        model: MappingDecisionModel,
        confirmation: MappingConfirmation,
    ) -> bool:
        request = confirmation.request
        return (
            model.source_product_id == request.source_product_id
            and model.canonical_product_id == request.canonical_product_id
            and model.canonical_product_version == request.canonical_product_version
            and model.scope == confirmation.scope.value
            and model.scope_user_id == confirmation.scope_user_id
            and model.source_code == request.source_code
            and model.reason_code == confirmation.reason_code
            and model.algorithm_version == confirmation.result.algorithm_version
        )

    @staticmethod
    def _snapshot(model: MappingDecisionModel) -> MappingDecision:
        return MappingDecision(
            id=model.id,
            source_product_id=model.source_product_id,
            canonical_product_id=model.canonical_product_id,
            canonical_product_version=model.canonical_product_version,
            actor_type=MappingActorType(model.actor_type),
            actor_internal_id=model.actor_internal_id,
            scope=MappingScope(model.scope),
            scope_user_id=model.scope_user_id,
            source_code=model.source_code,
            reason_code=model.reason_code,
            algorithm_version=model.algorithm_version,
            idempotency_key=model.idempotency_key,
            status=MappingDecisionStatus(model.status),
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
            revoked_at=model.revoked_at,
        )
