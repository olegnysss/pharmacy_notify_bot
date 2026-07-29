from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pharmacy_bot.application.catalog import CatalogService, RawProductIdentity
from pharmacy_bot.application.catalog_normalization import (
    CatalogNormalizer,
    NormalizationError,
)
from pharmacy_bot.domain.catalog import (
    AttributeProvenance,
    IdentifierTrust,
    ProductIdentifierInput,
    ProductKind,
    ProductQuality,
    QuantityDimension,
)


class Repository:
    def __init__(self) -> None:
        self.created = None

    async def create_or_get(
        self,
        identity,
        critical_signature,
        identifiers,
        provenance,
        *,
        now,
    ):
        self.created = (identity, critical_signature, identifiers, provenance, now)
        return object()

    async def revise(
        self,
        product_id,
        expected_version,
        identity,
        critical_signature,
        provenance,
        *,
        now,
    ):
        return object()


def test_quantities_are_canonical_and_idempotent_without_mixing_dimensions() -> None:
    normalizer = CatalogNormalizer()

    grams = normalizer.parse_quantity("0,5 г")
    milligrams = normalizer.parse_quantity("500 мг")
    volume = normalizer.parse_quantity("5 мл")

    assert grams.value == milligrams.value == Decimal("500.0")
    assert grams.unit == milligrams.unit == "mg"
    assert grams.dimension is QuantityDimension.MASS
    assert volume.dimension is QuantityDimension.VOLUME
    with pytest.raises(NormalizationError):
        normalizer.parse_concentration("5 мл/10 мг")


def test_critical_signature_preserves_dosage_and_form_differences() -> None:
    service = CatalogService(
        Repository(),
        CatalogNormalizer(),
        allowed_identifier_namespaces=("registration",),
    )
    tablet_10 = service.normalize_identity(
        RawProductIdentity(
            ProductKind.MEDICINE,
            "Тест",
            form="таб.",
            dosage="10 мг",
            quality=ProductQuality.VERIFIED,
        )
    )
    tablet_100 = service.normalize_identity(
        RawProductIdentity(
            ProductKind.MEDICINE,
            "  ТЕСТ ",
            form="таблетки",
            dosage="100 mg",
            quality=ProductQuality.VERIFIED,
        )
    )
    solution_10 = service.normalize_identity(
        RawProductIdentity(
            ProductKind.MEDICINE,
            "Тест",
            form="р-р",
            dosage="10 мг",
            quality=ProductQuality.VERIFIED,
        )
    )
    normalizer = CatalogNormalizer()

    assert tablet_10.trade_name_normalized == tablet_100.trade_name_normalized
    assert tablet_10.form_normalized == tablet_100.form_normalized == "таблетка"
    assert normalizer.critical_signature(tablet_10) != normalizer.critical_signature(tablet_100)
    assert normalizer.critical_signature(tablet_10) != normalizer.critical_signature(solution_10)


async def test_service_validates_namespace_deduplicates_ids_and_keeps_provenance() -> None:
    repository = Repository()
    service = CatalogService(
        repository,
        CatalogNormalizer(),
        allowed_identifier_namespaces=("registration",),
    )
    now = datetime(2026, 7, 29, 20, 0, tzinfo=UTC)
    identifier = ProductIdentifierInput(
        " Registration ",
        " РЛС-123 ",
        "Реестр",
        IdentifierTrust.AUTHORITATIVE,
    )
    provenance = (
        AttributeProvenance(
            "dosage",
            "registry",
            "record-123",
            "0,01 г",
            "10 mg",
            now,
            "v1",
        ),
    )

    await service.create_or_get(
        RawProductIdentity(
            ProductKind.MEDICINE,
            "Препарат",
            form="таблетки",
            dosage="10 мг",
            package_count=20,
            quality=ProductQuality.VERIFIED,
        ),
        (identifier, identifier),
        provenance,
        now=now,
    )

    assert repository.created
    assert repository.created[2] == (
        ProductIdentifierInput(
            "registration",
            "рлс-123",
            "Реестр",
            IdentifierTrust.AUTHORITATIVE,
        ),
    )
    assert repository.created[3] == provenance


def test_verified_medicine_cannot_hide_missing_critical_attributes() -> None:
    service = CatalogService(
        Repository(),
        CatalogNormalizer(),
        allowed_identifier_namespaces=("registration",),
    )

    with pytest.raises(NormalizationError):
        service.normalize_identity(
            RawProductIdentity(
                ProductKind.MEDICINE,
                "Препарат",
                form="таблетки",
                quality=ProductQuality.VERIFIED,
            )
        )
