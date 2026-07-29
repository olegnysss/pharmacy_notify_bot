from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.domain.source_product import (
    SourceProduct,
    SourceProductAttributes,
    SourceProductInput,
    SourceProductPage,
    SourceProductStatus,
)
from pharmacy_bot.infrastructure.models import (
    SourceProductModel,
    SourceProductVersionModel,
)


class SqlAlchemySourceProductRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(
        self,
        value: SourceProductInput,
        *,
        now: datetime,
    ) -> SourceProduct:
        async with self._session_factory.begin() as session:
            product_id = await session.scalar(
                insert(SourceProductModel)
                .values(
                    **self._input_values(value),
                    version=1,
                    first_seen_at=now,
                    last_seen_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        SourceProductModel.source_code,
                        SourceProductModel.external_id,
                    ]
                )
                .returning(SourceProductModel.id)
            )
            created = product_id is not None
            model = await self._locked_by_key(
                session,
                value.source_code,
                value.external_id,
            )
            if model is None:
                raise RuntimeError("source product was not created or found")
            if created:
                self._add_history(session, model, ("created",), now=now)
            elif model.semantic_fingerprint != value.semantic_fingerprint:
                changed_fields = self._changed_fields(model, value)
                for key, new_value in self._input_values(value).items():
                    setattr(model, key, new_value)
                model.version += 1
                model.last_seen_at = max(model.last_seen_at, now)
                model.updated_at = now
                self._add_history(session, model, changed_fields, now=now)
            else:
                model.last_seen_at = max(model.last_seen_at, now)
            await session.flush()
            await session.refresh(model)
            return self._snapshot(model)

    async def search(
        self,
        normalized_query: str,
        *,
        after_id: int | None,
        limit: int,
    ) -> SourceProductPage:
        async with self._session_factory() as session:
            statement = (
                select(SourceProductModel)
                .where(
                    SourceProductModel.status == SourceProductStatus.ACTIVE.value,
                    SourceProductModel.search_document.contains(
                        normalized_query,
                        autoescape=True,
                    ),
                )
                .order_by(SourceProductModel.id)
                .limit(limit + 1)
            )
            if after_id is not None:
                statement = statement.where(SourceProductModel.id > after_id)
            models = list((await session.scalars(statement)).all())
            has_more = len(models) > limit
            visible = models[:limit]
            return SourceProductPage(
                items=tuple(self._snapshot(model) for model in visible),
                next_after_id=visible[-1].id if has_more and visible else None,
            )

    @staticmethod
    def _input_values(value: SourceProductInput) -> dict[str, object]:
        return {
            "source_code": value.source_code,
            "external_id": value.external_id,
            "canonical_url": value.canonical_url,
            "raw_name": value.raw_name,
            "parsed_attributes": asdict(value.attributes),
            "status": value.status.value,
            "semantic_fingerprint": value.semantic_fingerprint,
            "search_document": value.search_document,
        }

    @classmethod
    def _changed_fields(
        cls,
        model: SourceProductModel,
        value: SourceProductInput,
    ) -> tuple[str, ...]:
        previous = {
            key: getattr(model, key)
            for key in (
                "canonical_url",
                "raw_name",
                "parsed_attributes",
                "status",
                "search_document",
            )
        }
        current = cls._input_values(value)
        return tuple(sorted(key for key, old in previous.items() if old != current[key]))

    @staticmethod
    def _add_history(
        session: AsyncSession,
        model: SourceProductModel,
        changed_fields: tuple[str, ...],
        *,
        now: datetime,
    ) -> None:
        session.add(
            SourceProductVersionModel(
                source_product_id=model.id,
                version=model.version,
                semantic_fingerprint=model.semantic_fingerprint,
                changed_fields=list(changed_fields),
                safe_snapshot={
                    "canonical_url": model.canonical_url,
                    "raw_name": model.raw_name,
                    "parsed_attributes": model.parsed_attributes,
                    "status": model.status,
                    "search_document": model.search_document,
                },
                observed_at=now,
            )
        )

    @staticmethod
    def _snapshot(model: SourceProductModel) -> SourceProduct:
        attributes = model.parsed_attributes
        return SourceProduct(
            id=model.id,
            source_code=model.source_code,
            external_id=model.external_id,
            canonical_url=model.canonical_url,
            raw_name=model.raw_name,
            attributes=SourceProductAttributes(
                kind=cast(str, attributes["kind"]),
                active_ingredient=cast(str | None, attributes.get("active_ingredient")),
                manufacturer=cast(str | None, attributes.get("manufacturer")),
                form=cast(str | None, attributes.get("form")),
                dosage=cast(str | None, attributes.get("dosage")),
                concentration=cast(str | None, attributes.get("concentration")),
                package_count=cast(int | None, attributes.get("package_count")),
                volume=cast(str | None, attributes.get("volume")),
                route=cast(str | None, attributes.get("route")),
                package_variant=cast(str | None, attributes.get("package_variant")),
            ),
            status=SourceProductStatus(model.status),
            semantic_fingerprint=model.semantic_fingerprint,
            search_document=model.search_document,
            canonical_product_id=model.canonical_product_id,
            canonical_product_version=model.canonical_product_version,
            version=model.version,
            first_seen_at=model.first_seen_at,
            last_seen_at=model.last_seen_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    async def _locked_by_key(
        session: AsyncSession,
        source_code: str,
        external_id: str,
    ) -> SourceProductModel | None:
        return cast(
            SourceProductModel | None,
            await session.scalar(
                select(SourceProductModel)
                .where(
                    SourceProductModel.source_code == source_code,
                    SourceProductModel.external_id == external_id,
                )
                .with_for_update()
            ),
        )
