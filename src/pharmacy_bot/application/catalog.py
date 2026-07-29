from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pharmacy_bot.application.catalog_normalization import (
    CatalogNormalizer,
    NormalizationError,
)
from pharmacy_bot.domain.catalog import (
    AttributeProvenance,
    CanonicalProduct,
    ProductIdentifierInput,
    ProductIdentityInput,
    ProductKind,
    ProductQuality,
)

_IDENTIFIER_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_IDENTIFIER_VALUE = re.compile(r"^[^\s]{2,256}$")


@dataclass(frozen=True, slots=True)
class RawProductIdentity:
    kind: ProductKind
    trade_name: str
    active_ingredient: str | None = None
    manufacturer: str | None = None
    form: str | None = None
    dosage: str | None = None
    concentration: str | None = None
    package_count: int | None = None
    volume: str | None = None
    route: str | None = None
    package_variant: str | None = None
    quality: ProductQuality = ProductQuality.PARTIAL


class CatalogRepository(Protocol):
    async def create_or_get(
        self,
        identity: ProductIdentityInput,
        critical_signature: str,
        identifiers: tuple[ProductIdentifierInput, ...],
        provenance: tuple[AttributeProvenance, ...],
        *,
        now: datetime,
    ) -> CanonicalProduct: ...

    async def revise(
        self,
        product_id: int,
        expected_version: int,
        identity: ProductIdentityInput,
        critical_signature: str,
        provenance: tuple[AttributeProvenance, ...],
        *,
        now: datetime,
    ) -> CanonicalProduct: ...


class CatalogService:
    def __init__(
        self,
        repository: CatalogRepository,
        normalizer: CatalogNormalizer,
        *,
        allowed_identifier_namespaces: tuple[str, ...],
    ) -> None:
        self._repository = repository
        self._normalizer = normalizer
        self._namespaces = frozenset(item.casefold() for item in allowed_identifier_namespaces)

    async def create_or_get(
        self,
        raw: RawProductIdentity,
        identifiers: tuple[ProductIdentifierInput, ...],
        provenance: tuple[AttributeProvenance, ...],
        *,
        now: datetime,
    ) -> CanonicalProduct:
        identity = self.normalize_identity(raw)
        return await self._repository.create_or_get(
            identity,
            self._normalizer.critical_signature(identity),
            self.normalize_identifiers(identifiers),
            self._validate_provenance(provenance),
            now=now,
        )

    async def revise(
        self,
        product_id: int,
        expected_version: int,
        raw: RawProductIdentity,
        provenance: tuple[AttributeProvenance, ...],
        *,
        now: datetime,
    ) -> CanonicalProduct:
        identity = self.normalize_identity(raw)
        return await self._repository.revise(
            product_id,
            expected_version,
            identity,
            self._normalizer.critical_signature(identity),
            self._validate_provenance(provenance),
            now=now,
        )

    def normalize_identity(self, raw: RawProductIdentity) -> ProductIdentityInput:
        trade_name = self._required_text(raw.trade_name, "trade name", 512)
        active = self._optional_text(raw.active_ingredient, 512)
        manufacturer = self._optional_text(raw.manufacturer, 512)
        form = self._optional_text(raw.form, 128)
        route = self._optional_text(raw.route, 128)
        package_variant = self._optional_text(raw.package_variant, 256)
        if raw.package_count is not None and raw.package_count <= 0:
            raise NormalizationError("package count must be positive")
        concentration = (
            self._normalizer.parse_concentration(raw.concentration) if raw.concentration else None
        )
        identity = ProductIdentityInput(
            kind=raw.kind,
            trade_name_raw=trade_name,
            trade_name_normalized=self._normalizer.normalize_text(trade_name),
            active_ingredient_raw=active,
            active_ingredient_normalized=(
                self._normalizer.normalize_text(active) if active else None
            ),
            manufacturer_raw=manufacturer,
            manufacturer_normalized=(
                self._normalizer.normalize_text(manufacturer) if manufacturer else None
            ),
            form_raw=form,
            form_normalized=self._normalizer.normalize_form(form) if form else None,
            dosage=self._normalizer.parse_quantity(raw.dosage) if raw.dosage else None,
            concentration_numerator=concentration.numerator if concentration else None,
            concentration_denominator=concentration.denominator if concentration else None,
            package_count=raw.package_count,
            volume=self._normalizer.parse_quantity(raw.volume) if raw.volume else None,
            route_raw=route,
            route_normalized=self._normalizer.normalize_text(route) if route else None,
            package_variant_raw=package_variant,
            package_variant_normalized=(
                self._normalizer.normalize_text(package_variant) if package_variant else None
            ),
            quality=raw.quality,
        )
        if (
            raw.kind is ProductKind.MEDICINE
            and raw.quality is ProductQuality.VERIFIED
            and (
                identity.form_normalized is None
                or (identity.dosage is None and identity.concentration_numerator is None)
            )
        ):
            raise NormalizationError("verified medicine requires form and dosage or concentration")
        return identity

    def normalize_identifiers(
        self,
        identifiers: tuple[ProductIdentifierInput, ...],
    ) -> tuple[ProductIdentifierInput, ...]:
        normalized = []
        seen: set[tuple[str, str]] = set()
        for item in identifiers:
            namespace = unicodedata.normalize("NFKC", item.namespace).strip().casefold()
            value = unicodedata.normalize("NFKC", item.value).strip().casefold()
            issuer = self._required_text(item.issuer, "identifier issuer", 256)
            if namespace not in self._namespaces or not _IDENTIFIER_NAMESPACE.fullmatch(namespace):
                raise NormalizationError("identifier namespace is not allowed")
            if not _IDENTIFIER_VALUE.fullmatch(value):
                raise NormalizationError("identifier value is invalid")
            key = (namespace, value)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(ProductIdentifierInput(namespace, value, issuer, item.trust))
        return tuple(normalized)

    @staticmethod
    def _validate_provenance(
        values: tuple[AttributeProvenance, ...],
    ) -> tuple[AttributeProvenance, ...]:
        for item in values:
            if not item.field_name or len(item.field_name) > 64:
                raise NormalizationError("provenance field name is invalid")
            if not item.source_kind or len(item.source_kind) > 64:
                raise NormalizationError("provenance source kind is invalid")
            if not item.source_reference or len(item.source_reference) > 256:
                raise NormalizationError("provenance reference is invalid")
            if len(item.raw_value or "") > 1024 or len(item.normalized_value or "") > 1024:
                raise NormalizationError("provenance value is too long")
            if not item.data_version or len(item.data_version) > 128:
                raise NormalizationError("provenance data version is invalid")
        return values

    @staticmethod
    def _required_text(value: str, label: str, max_length: int) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized or len(normalized) > max_length:
            raise NormalizationError(f"{label} length is invalid")
        return normalized

    @staticmethod
    def _optional_text(value: str | None, max_length: int) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise NormalizationError("attribute length is invalid")
        return normalized
