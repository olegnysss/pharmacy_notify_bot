from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

import pytest

from pharmacy_bot.application.catalog_normalization import CatalogNormalizer
from pharmacy_bot.application.source_products import (
    IncompleteSourcePayload,
    SourcePayloadParser,
    SourcePayloadValidationError,
    SourceProductService,
    SourceRegistration,
    UnknownSourceError,
)
from pharmacy_bot.domain.source_product import (
    SourceProduct,
    SourceProductInput,
    SourceProductPage,
)


class MemorySourceProducts:
    def __init__(self) -> None:
        self.value: SourceProductInput | None = None

    async def upsert(
        self,
        value: SourceProductInput,
        *,
        now: datetime,
    ) -> SourceProduct:
        self.value = value
        return SourceProduct(
            id=1,
            source_code=value.source_code,
            external_id=value.external_id,
            canonical_url=value.canonical_url,
            raw_name=value.raw_name,
            attributes=value.attributes,
            status=value.status,
            semantic_fingerprint=value.semantic_fingerprint,
            search_document=value.search_document,
            canonical_product_id=None,
            canonical_product_version=None,
            version=1,
            first_seen_at=now,
            last_seen_at=now,
            updated_at=now,
        )

    async def search(
        self,
        normalized_query: str,
        *,
        after_id: int | None,
        limit: int,
    ) -> SourceProductPage:
        del normalized_query, after_id, limit
        return SourceProductPage((), None)


@pytest.fixture
def parser() -> SourcePayloadParser:
    return SourcePayloadParser(
        CatalogNormalizer(),
        (SourceRegistration("test_source", ("pharmacy.example",)),),
        max_payload_bytes=1024,
    )


def payload(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "external_id": "product-123",
        "canonical_url": "https://shop.pharmacy.example/product/123?region=77",
        "raw_name": "Тест 10 мг, таблетки №20",
        "kind": "medicine",
        "form": "таб.",
        "dosage": "0,01 г",
        "package_count": 20,
        "manufacturer": "Производитель",
    }
    value.update(changes)
    return value


def test_parser_extracts_distinguishing_attributes_without_inventing_missing_values(
    parser: SourcePayloadParser,
) -> None:
    result = parser.parse("TEST_SOURCE", payload())

    assert result.source_code == "test_source"
    assert result.attributes.form == "таблетка"
    assert result.attributes.dosage == "10 mg"
    assert result.attributes.concentration is None
    assert "таблетка" in result.search_document
    assert "10 mg" in result.search_document
    assert "20" in result.search_document


def test_fingerprint_is_stable_and_changes_for_dosage(
    parser: SourcePayloadParser,
) -> None:
    first = parser.parse("test_source", payload())
    same = parser.parse("test_source", dict(reversed(tuple(payload().items()))))
    changed = parser.parse("test_source", payload(dosage="20 мг"))

    assert first.semantic_fingerprint == same.semantic_fingerprint
    assert first.semantic_fingerprint != changed.semantic_fingerprint


@pytest.mark.parametrize(
    ("source_code", "value", "error"),
    [
        ("unknown", payload(), UnknownSourceError),
        ("test_source", {"external_id": "x"}, IncompleteSourcePayload),
        (
            "test_source",
            payload(canonical_url="https://evil.example/product/123"),
            SourcePayloadValidationError,
        ),
        (
            "test_source",
            payload(raw_name="<script>alert(1)</script>"),
            SourcePayloadValidationError,
        ),
        (
            "test_source",
            payload(headers={"Authorization": "secret"}),
            SourcePayloadValidationError,
        ),
        (
            "test_source",
            payload(raw_name="x" * 2_000),
            SourcePayloadValidationError,
        ),
        (
            "test_source",
            payload(dosage="неизвестно"),
            SourcePayloadValidationError,
        ),
    ],
)
def test_parser_rejects_untrusted_or_broken_payloads_without_echoing_data(
    parser: SourcePayloadParser,
    source_code: str,
    value: Mapping[str, object],
    error: type[Exception],
) -> None:
    with pytest.raises(error) as raised:
        parser.parse(source_code, value)

    assert "secret" not in str(raised.value)
    assert "<script>" not in str(raised.value)


async def test_service_rejects_unbounded_page_size(parser: SourcePayloadParser) -> None:
    service = SourceProductService(
        MemorySourceProducts(),
        parser,
        CatalogNormalizer(),
        max_page_size=25,
    )

    with pytest.raises(SourcePayloadValidationError):
        await service.search("тест", page_size=26)


def test_registration_rejects_duplicate_or_invalid_source() -> None:
    with pytest.raises(ValueError):
        SourcePayloadParser(
            CatalogNormalizer(),
            (
                SourceRegistration("same", ("one.example",)),
                SourceRegistration("same", ("two.example",)),
            ),
        )
