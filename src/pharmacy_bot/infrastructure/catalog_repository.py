from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pharmacy_bot.domain.catalog import (
    AttributeProvenance,
    CanonicalProduct,
    CatalogConflict,
    IdentifierStatus,
    NormalizedQuantity,
    ProductIdentifierInput,
    ProductIdentityInput,
    ProductKind,
    ProductQuality,
    QuantityDimension,
    StaleCatalogVersion,
)
from pharmacy_bot.infrastructure.models import (
    CanonicalProductModel,
    CanonicalProductVersionModel,
    ProductAttributeProvenanceModel,
    ProductIdentifierModel,
)


class SqlAlchemyCatalogRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_or_get(
        self,
        identity: ProductIdentityInput,
        critical_signature: str,
        identifiers: tuple[ProductIdentifierInput, ...],
        provenance: tuple[AttributeProvenance, ...],
        *,
        now: datetime,
    ) -> CanonicalProduct:
        async with self._session_factory.begin() as session:
            product_id = await session.scalar(
                insert(CanonicalProductModel)
                .values(
                    version=1,
                    critical_signature=critical_signature,
                    **self._identity_values(identity),
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[CanonicalProductModel.critical_signature])
                .returning(CanonicalProductModel.id)
            )
            created = product_id is not None
            if product_id is None:
                product_id = await session.scalar(
                    select(CanonicalProductModel.id).where(
                        CanonicalProductModel.critical_signature == critical_signature
                    )
                )
            if product_id is None:
                raise RuntimeError("canonical product was not created or found")
            model = await self._product_locked(session, int(product_id))
            if model is None:
                raise RuntimeError("canonical product disappeared")
            if created:
                session.add(
                    CanonicalProductVersionModel(
                        product_id=model.id,
                        version=1,
                        identity_snapshot=self._identity_snapshot(identity),
                        critical_signature=critical_signature,
                        created_at=now,
                    )
                )
                self._add_provenance(session, model.id, 1, provenance)
            await self._attach_identifiers(
                session,
                model.id,
                identifiers,
                now=now,
            )
            await session.flush()
            return self._snapshot(model)

    async def revise(
        self,
        product_id: int,
        expected_version: int,
        identity: ProductIdentityInput,
        critical_signature: str,
        provenance: tuple[AttributeProvenance, ...],
        *,
        now: datetime,
    ) -> CanonicalProduct:
        async with self._session_factory.begin() as session:
            model = await self._product_locked(session, product_id)
            if model is None:
                raise CatalogConflict("canonical product does not exist")
            if model.version != expected_version:
                raise StaleCatalogVersion
            owner = await session.scalar(
                select(CanonicalProductModel.id).where(
                    CanonicalProductModel.critical_signature == critical_signature,
                    CanonicalProductModel.id != product_id,
                )
            )
            if owner is not None:
                raise CatalogConflict("critical identity already belongs to another product")
            new_version = model.version + 1
            for key, value in self._identity_values(identity).items():
                setattr(model, key, value)
            model.critical_signature = critical_signature
            model.version = new_version
            model.updated_at = now
            session.add(
                CanonicalProductVersionModel(
                    product_id=model.id,
                    version=new_version,
                    identity_snapshot=self._identity_snapshot(identity),
                    critical_signature=critical_signature,
                    created_at=now,
                )
            )
            self._add_provenance(session, model.id, new_version, provenance)
            await session.flush()
            await session.refresh(model)
            return self._snapshot(model)

    async def revoke_identifier(
        self,
        product_id: int,
        namespace: str,
        value: str,
        *,
        now: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            identifier_id = await session.scalar(
                update(ProductIdentifierModel)
                .where(
                    ProductIdentifierModel.product_id == product_id,
                    ProductIdentifierModel.namespace == namespace,
                    ProductIdentifierModel.value == value,
                    ProductIdentifierModel.status == IdentifierStatus.ACTIVE.value,
                )
                .values(status=IdentifierStatus.REVOKED.value, revoked_at=now)
                .returning(ProductIdentifierModel.id)
            )
            return identifier_id is not None

    async def get(self, product_id: int) -> CanonicalProduct | None:
        async with self._session_factory() as session:
            model = await session.get(CanonicalProductModel, product_id)
            return self._snapshot(model) if model else None

    async def _attach_identifiers(
        self,
        session: AsyncSession,
        product_id: int,
        identifiers: tuple[ProductIdentifierInput, ...],
        *,
        now: datetime,
    ) -> None:
        for identifier in identifiers:
            inserted_owner = await session.scalar(
                insert(ProductIdentifierModel)
                .values(
                    product_id=product_id,
                    namespace=identifier.namespace,
                    value=identifier.value,
                    issuer=identifier.issuer,
                    trust=identifier.trust.value,
                    status=IdentifierStatus.ACTIVE.value,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        ProductIdentifierModel.namespace,
                        ProductIdentifierModel.value,
                    ]
                )
                .returning(ProductIdentifierModel.product_id)
            )
            if inserted_owner is None:
                existing_owner = await session.scalar(
                    select(ProductIdentifierModel.product_id).where(
                        ProductIdentifierModel.namespace == identifier.namespace,
                        ProductIdentifierModel.value == identifier.value,
                    )
                )
                if existing_owner != product_id:
                    raise CatalogConflict(
                        "identifier is already assigned to another canonical product"
                    )

    @staticmethod
    def _add_provenance(
        session: AsyncSession,
        product_id: int,
        product_version: int,
        values: tuple[AttributeProvenance, ...],
    ) -> None:
        session.add_all(
            ProductAttributeProvenanceModel(
                product_id=product_id,
                product_version=product_version,
                field_name=item.field_name,
                source_kind=item.source_kind,
                source_reference=item.source_reference,
                raw_value=item.raw_value,
                normalized_value=item.normalized_value,
                observed_at=item.observed_at,
                data_version=item.data_version,
            )
            for item in values
        )

    @staticmethod
    def _identity_values(identity: ProductIdentityInput) -> dict[str, object]:
        values: dict[str, object] = {
            "kind": identity.kind.value,
            "quality": identity.quality.value,
            "trade_name_raw": identity.trade_name_raw,
            "trade_name_normalized": identity.trade_name_normalized,
            "active_ingredient_raw": identity.active_ingredient_raw,
            "active_ingredient_normalized": identity.active_ingredient_normalized,
            "manufacturer_raw": identity.manufacturer_raw,
            "manufacturer_normalized": identity.manufacturer_normalized,
            "form_raw": identity.form_raw,
            "form_normalized": identity.form_normalized,
            "package_count": identity.package_count,
            "route_raw": identity.route_raw,
            "route_normalized": identity.route_normalized,
            "package_variant_raw": identity.package_variant_raw,
            "package_variant_normalized": identity.package_variant_normalized,
        }
        values.update(SqlAlchemyCatalogRepository._quantity_values("dosage", identity.dosage))
        values.update(
            SqlAlchemyCatalogRepository._quantity_values(
                "concentration_numerator",
                identity.concentration_numerator,
            )
        )
        values.update(
            SqlAlchemyCatalogRepository._quantity_values(
                "concentration_denominator",
                identity.concentration_denominator,
            )
        )
        values.update(SqlAlchemyCatalogRepository._quantity_values("volume", identity.volume))
        return values

    @staticmethod
    def _quantity_values(
        prefix: str,
        value: NormalizedQuantity | None,
    ) -> dict[str, object]:
        return {
            f"{prefix}_value": value.value if value else None,
            f"{prefix}_unit": value.unit if value else None,
            f"{prefix}_dimension": value.dimension.value if value else None,
        }

    @classmethod
    def _identity_snapshot(cls, identity: ProductIdentityInput) -> dict[str, object]:
        values = cls._identity_values(identity)
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in values.items()
        }

    @classmethod
    def _snapshot(cls, model: CanonicalProductModel) -> CanonicalProduct:
        identity = ProductIdentityInput(
            kind=ProductKind(model.kind),
            trade_name_raw=model.trade_name_raw,
            trade_name_normalized=model.trade_name_normalized,
            active_ingredient_raw=model.active_ingredient_raw,
            active_ingredient_normalized=model.active_ingredient_normalized,
            manufacturer_raw=model.manufacturer_raw,
            manufacturer_normalized=model.manufacturer_normalized,
            form_raw=model.form_raw,
            form_normalized=model.form_normalized,
            dosage=cls._quantity(
                model.dosage_value,
                model.dosage_unit,
                model.dosage_dimension,
                model.dosage_value,
            ),
            concentration_numerator=cls._quantity(
                model.concentration_numerator_value,
                model.concentration_numerator_unit,
                model.concentration_numerator_dimension,
                model.concentration_numerator_value,
            ),
            concentration_denominator=cls._quantity(
                model.concentration_denominator_value,
                model.concentration_denominator_unit,
                model.concentration_denominator_dimension,
                model.concentration_denominator_value,
            ),
            package_count=model.package_count,
            volume=cls._quantity(
                model.volume_value,
                model.volume_unit,
                model.volume_dimension,
                model.volume_value,
            ),
            route_raw=model.route_raw,
            route_normalized=model.route_normalized,
            package_variant_raw=model.package_variant_raw,
            package_variant_normalized=model.package_variant_normalized,
            quality=ProductQuality(model.quality),
        )
        return CanonicalProduct(
            id=model.id,
            version=model.version,
            identity=identity,
            critical_signature=model.critical_signature,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _quantity(
        value: Decimal | None,
        unit: str | None,
        dimension: str | None,
        raw_value: Decimal | None,
    ) -> NormalizedQuantity | None:
        if value is None or unit is None or dimension is None:
            return None
        return NormalizedQuantity(
            value=value,
            unit=unit,
            dimension=QuantityDimension(dimension),
            raw=str(raw_value),
        )

    @staticmethod
    async def _product_locked(
        session: AsyncSession,
        product_id: int,
    ) -> CanonicalProductModel | None:
        return cast(
            CanonicalProductModel | None,
            await session.scalar(
                select(CanonicalProductModel)
                .where(CanonicalProductModel.id == product_id)
                .with_for_update()
            ),
        )
