from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.domain.source_registry import (
    LegalUsageStatus,
    Source,
    SourceConfiguration,
    SourceLimits,
    SourceOperation,
    SourceRegistryConflict,
    SourceStatus,
    SourceType,
    StaleSourceVersion,
)
from pharmacy_bot.infrastructure.models import SourceModel, SourceVersionModel


class SqlAlchemySourceRegistryRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_or_get(
        self,
        configuration: SourceConfiguration,
        fingerprint: str,
        *,
        now: datetime,
    ) -> Source:
        async with self._session_factory.begin() as session:
            source_id = await session.scalar(
                insert(SourceModel)
                .values(
                    version=1,
                    fingerprint=fingerprint,
                    **self._values(configuration),
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[SourceModel.code])
                .returning(SourceModel.id)
            )
            created = source_id is not None
            model = await session.scalar(
                select(SourceModel).where(SourceModel.code == configuration.code)
            )
            if model is None:
                raise RuntimeError("source was not created or found")
            if not created and model.fingerprint != fingerprint:
                raise SourceRegistryConflict(
                    "source code already has another configuration; revise it explicitly"
                )
            if created:
                self._add_version(
                    session,
                    model,
                    configuration,
                    fingerprint,
                    actor_internal_id=None,
                    reason="registered",
                    now=now,
                )
            return self._snapshot(model)

    async def revise(
        self,
        source_id: int,
        expected_version: int,
        configuration: SourceConfiguration,
        fingerprint: str,
        *,
        actor_internal_id: int,
        reason: str,
        now: datetime,
    ) -> Source:
        async with self._session_factory.begin() as session:
            model = cast(
                SourceModel | None,
                await session.scalar(
                    select(SourceModel).where(SourceModel.id == source_id).with_for_update()
                ),
            )
            if model is None:
                raise SourceRegistryConflict("source does not exist")
            if model.version != expected_version:
                raise StaleSourceVersion
            if model.code != configuration.code:
                raise SourceRegistryConflict("source code is immutable")
            if model.fingerprint == fingerprint:
                return self._snapshot(model)
            model.version += 1
            model.fingerprint = fingerprint
            model.updated_at = now
            for key, value in self._values(configuration).items():
                setattr(model, key, value)
            self._add_version(
                session,
                model,
                configuration,
                fingerprint,
                actor_internal_id=actor_internal_id,
                reason=reason,
                now=now,
            )
            await session.flush()
            await session.refresh(model)
            return self._snapshot(model)

    @staticmethod
    def _values(value: SourceConfiguration) -> dict[str, object]:
        return {
            "code": value.code,
            "name": value.name,
            "source_type": value.source_type.value,
            "status": value.status.value,
            "legal_status": value.legal_status.value,
            "adapter_version": value.adapter_version,
            "capability_version": value.capability_version,
            "capabilities": sorted(item.value for item in value.capabilities),
            "base_urls": list(value.base_urls),
            "redirect_hosts": list(value.redirect_hosts),
            "requests_per_window": value.limits.requests_per_window,
            "window_seconds": value.limits.window_seconds,
            "max_concurrency": value.limits.max_concurrency,
            "freshness_seconds": value.limits.freshness_seconds,
            "cache_ttl_seconds": value.limits.cache_ttl_seconds,
        }

    @classmethod
    def _add_version(
        cls,
        session: AsyncSession,
        model: SourceModel,
        configuration: SourceConfiguration,
        fingerprint: str,
        *,
        actor_internal_id: int | None,
        reason: str,
        now: datetime,
    ) -> None:
        session.add(
            SourceVersionModel(
                source_id=model.id,
                version=model.version,
                fingerprint=fingerprint,
                safe_snapshot=cls._values(configuration),
                actor_internal_id=actor_internal_id,
                reason=reason,
                created_at=now,
            )
        )

    @staticmethod
    def _snapshot(model: SourceModel) -> Source:
        return Source(
            model.id,
            model.version,
            SourceConfiguration(
                model.code,
                model.name,
                SourceType(model.source_type),
                SourceStatus(model.status),
                LegalUsageStatus(model.legal_status),
                model.adapter_version,
                model.capability_version,
                frozenset(SourceOperation(item) for item in model.capabilities),
                tuple(model.base_urls),
                tuple(model.redirect_hosts),
                SourceLimits(
                    model.requests_per_window,
                    model.window_seconds,
                    model.max_concurrency,
                    model.freshness_seconds,
                    model.cache_ttl_seconds,
                ),
            ),
            model.fingerprint,
            model.created_at,
            model.updated_at,
        )
