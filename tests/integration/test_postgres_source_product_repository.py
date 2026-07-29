from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.catalog_normalization import CatalogNormalizer
from pharmacy_bot.application.source_products import (
    SourcePayloadParser,
    SourceProductService,
    SourceRegistration,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    SourceProductModel,
    SourceProductVersionModel,
)
from pharmacy_bot.infrastructure.source_product_repository import (
    SqlAlchemySourceProductRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


def payload(*, dosage: str = "10 мг", name: str = "Тест") -> dict[str, object]:
    return {
        "external_id": "external-123",
        "canonical_url": "https://pharmacy.example/products/external-123",
        "raw_name": name,
        "form": "таблетки",
        "dosage": dosage,
        "package_count": 20,
        "manufacturer": "Производитель",
    }


async def test_source_product_upsert_is_idempotent_versioned_and_searchable(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemySourceProductRepository(session_factory)
    service = SourceProductService(
        repository,
        SourcePayloadParser(
            CatalogNormalizer(),
            (SourceRegistration("test_source", ("pharmacy.example",)),),
        ),
        CatalogNormalizer(),
        max_page_size=2,
    )
    now = datetime(2026, 7, 29, 21, 0, tzinfo=UTC)
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(SourceProductVersionModel))
            await session.execute(delete(SourceProductModel))

        first, repeated = await asyncio.gather(
            service.ingest("test_source", payload(), now=now),
            service.ingest("test_source", payload(), now=now + timedelta(seconds=1)),
        )
        changed = await service.ingest(
            "test_source",
            payload(dosage="20 мг", name="Тест обновлённый"),
            now=now + timedelta(minutes=1),
        )
        stale = await service.ingest(
            "test_source",
            payload(dosage="30 мг", name="Устаревший ответ"),
            now=now - timedelta(minutes=1),
        )
        page = await service.search("тест", page_size=1)

        async with session_factory() as session:
            product_count = await session.scalar(
                select(func.count()).select_from(SourceProductModel)
            )
            versions = list(
                (
                    await session.scalars(
                        select(SourceProductVersionModel).order_by(
                            SourceProductVersionModel.version
                        )
                    )
                ).all()
            )

        assert first.id == repeated.id == changed.id
        assert first.version == repeated.version == 1
        assert changed.version == 2
        assert stale.version == 2
        assert stale.attributes.dosage == "20 mg"
        assert changed.first_seen_at == now
        assert changed.last_seen_at == now + timedelta(minutes=1)
        assert product_count == 1
        assert [item.version for item in versions] == [1, 2]
        assert versions[0].changed_fields == ["created"]
        assert "parsed_attributes" in versions[1].changed_fields
        assert "raw_name" in versions[1].changed_fields
        assert len(page.items) == 1
        assert page.items[0].attributes.dosage == "20 mg"
        assert "таблетка" in page.items[0].search_document
        assert "20" in page.items[0].search_document
    finally:
        await engine.dispose()
