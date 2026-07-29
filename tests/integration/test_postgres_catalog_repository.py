from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.catalog import CatalogService, RawProductIdentity
from pharmacy_bot.application.catalog_normalization import CatalogNormalizer
from pharmacy_bot.domain.catalog import (
    AttributeProvenance,
    CatalogConflict,
    IdentifierTrust,
    ProductIdentifierInput,
    ProductKind,
    ProductQuality,
    StaleCatalogVersion,
)
from pharmacy_bot.infrastructure.catalog_repository import SqlAlchemyCatalogRepository
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    CanonicalProductModel,
    CanonicalProductVersionModel,
    ProductAttributeProvenanceModel,
    ProductIdentifierModel,
    SubscriptionModel,
    SubscriptionSetupDraftModel,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


async def test_catalog_is_idempotent_versioned_and_protects_identifiers(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyCatalogRepository(session_factory)
    service = CatalogService(
        repository,
        CatalogNormalizer(),
        allowed_identifier_namespaces=("registration", "gtin"),
    )
    now = datetime(2026, 7, 29, 20, 0, tzinfo=UTC)
    identifier = ProductIdentifierInput(
        "registration",
        "reg-123",
        "Official registry",
        IdentifierTrust.AUTHORITATIVE,
    )
    provenance = (
        AttributeProvenance(
            "trade_name",
            "registry",
            "record-123",
            "Тест",
            "тест",
            now,
            "registry-v1",
        ),
    )
    raw = RawProductIdentity(
        ProductKind.MEDICINE,
        "Тест",
        active_ingredient="Вещество",
        manufacturer="Производитель",
        form="таблетки",
        dosage="10 мг",
        package_count=20,
        quality=ProductQuality.VERIFIED,
    )
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(SubscriptionModel))
            await session.execute(delete(SubscriptionSetupDraftModel))
            await session.execute(delete(ProductAttributeProvenanceModel))
            await session.execute(delete(ProductIdentifierModel))
            await session.execute(delete(CanonicalProductVersionModel))
            await session.execute(delete(CanonicalProductModel))

        first = await service.create_or_get(
            raw,
            (identifier,),
            provenance,
            now=now,
        )
        repeated = await service.create_or_get(
            raw,
            (identifier,),
            provenance,
            now=now,
        )
        with pytest.raises(CatalogConflict):
            await service.create_or_get(
                RawProductIdentity(
                    ProductKind.MEDICINE,
                    "Другой товар",
                    form="таблетки",
                    dosage="20 мг",
                    quality=ProductQuality.VERIFIED,
                ),
                (identifier,),
                (),
                now=now,
            )
        revised = await service.revise(
            first.id,
            first.version,
            RawProductIdentity(
                ProductKind.MEDICINE,
                "Тест",
                active_ingredient="Вещество",
                manufacturer="Новый производитель",
                form="таблетки",
                dosage="10 мг",
                package_count=20,
                quality=ProductQuality.VERIFIED,
            ),
            provenance,
            now=now,
        )
        with pytest.raises(StaleCatalogVersion):
            await service.revise(
                first.id,
                first.version,
                raw,
                (),
                now=now,
            )
        revoked = await repository.revoke_identifier(
            first.id,
            "registration",
            "reg-123",
            now=now,
        )
        async with session_factory() as session:
            product_count = await session.scalar(
                select(func.count()).select_from(CanonicalProductModel)
            )
            version_count = await session.scalar(
                select(func.count()).select_from(CanonicalProductVersionModel)
            )
            identifier_count = await session.scalar(
                select(func.count()).select_from(ProductIdentifierModel)
            )
            provenance_count = await session.scalar(
                select(func.count()).select_from(ProductAttributeProvenanceModel)
            )
            stored_identifier = await session.scalar(select(ProductIdentifierModel))

        assert first.id == repeated.id
        assert repeated.version == 1
        assert revised.version == 2
        assert product_count == 1
        assert version_count == 2
        assert identifier_count == 1
        assert provenance_count == 2
        assert revoked
        assert stored_identifier and stored_identifier.status == "revoked"
    finally:
        await engine.dispose()
