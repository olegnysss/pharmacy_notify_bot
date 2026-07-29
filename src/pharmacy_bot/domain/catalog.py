from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ProductKind(StrEnum):
    MEDICINE = "medicine"
    OTHER = "other"


class ProductQuality(StrEnum):
    PARTIAL = "partial"
    VERIFIED = "verified"
    RETIRED = "retired"


class IdentifierTrust(StrEnum):
    AUTHORITATIVE = "authoritative"
    TRUSTED_SOURCE = "trusted_source"
    UNVERIFIED = "unverified"


class IdentifierStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class QuantityDimension(StrEnum):
    MASS = "mass"
    VOLUME = "volume"
    COUNT = "count"


@dataclass(frozen=True, slots=True)
class NormalizedQuantity:
    value: Decimal
    unit: str
    dimension: QuantityDimension
    raw: str


@dataclass(frozen=True, slots=True)
class ProductIdentityInput:
    kind: ProductKind
    trade_name_raw: str
    trade_name_normalized: str
    active_ingredient_raw: str | None = None
    active_ingredient_normalized: str | None = None
    manufacturer_raw: str | None = None
    manufacturer_normalized: str | None = None
    form_raw: str | None = None
    form_normalized: str | None = None
    dosage: NormalizedQuantity | None = None
    concentration_numerator: NormalizedQuantity | None = None
    concentration_denominator: NormalizedQuantity | None = None
    package_count: int | None = None
    volume: NormalizedQuantity | None = None
    route_raw: str | None = None
    route_normalized: str | None = None
    package_variant_raw: str | None = None
    package_variant_normalized: str | None = None
    quality: ProductQuality = ProductQuality.PARTIAL


@dataclass(frozen=True, slots=True)
class ProductIdentifierInput:
    namespace: str
    value: str
    issuer: str
    trust: IdentifierTrust


@dataclass(frozen=True, slots=True)
class AttributeProvenance:
    field_name: str
    source_kind: str
    source_reference: str
    raw_value: str | None
    normalized_value: str | None
    observed_at: datetime
    data_version: str


@dataclass(frozen=True, slots=True)
class CanonicalProduct:
    id: int
    version: int
    identity: ProductIdentityInput
    critical_signature: str
    created_at: datetime
    updated_at: datetime


class CatalogConflict(Exception):
    pass


class StaleCatalogVersion(Exception):
    pass
