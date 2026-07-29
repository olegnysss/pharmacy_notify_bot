from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from pharmacy_bot.domain.catalog import (
    NormalizedQuantity,
    ProductIdentityInput,
    QuantityDimension,
)

_SPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s+%./-]", re.UNICODE)
_QUANTITY = re.compile(
    r"^\s*(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>[a-zа-яёµμ]+|шт\.?)\s*$",
    re.IGNORECASE,
)

_UNITS: dict[str, tuple[Decimal, str, QuantityDimension]] = {
    "мкг": (Decimal("0.001"), "mg", QuantityDimension.MASS),
    "µg": (Decimal("0.001"), "mg", QuantityDimension.MASS),
    "μg": (Decimal("0.001"), "mg", QuantityDimension.MASS),
    "mcg": (Decimal("0.001"), "mg", QuantityDimension.MASS),
    "мг": (Decimal("1"), "mg", QuantityDimension.MASS),
    "mg": (Decimal("1"), "mg", QuantityDimension.MASS),
    "г": (Decimal("1000"), "mg", QuantityDimension.MASS),
    "g": (Decimal("1000"), "mg", QuantityDimension.MASS),
    "мл": (Decimal("1"), "ml", QuantityDimension.VOLUME),
    "ml": (Decimal("1"), "ml", QuantityDimension.VOLUME),
    "л": (Decimal("1000"), "ml", QuantityDimension.VOLUME),
    "l": (Decimal("1000"), "ml", QuantityDimension.VOLUME),
    "шт": (Decimal("1"), "unit", QuantityDimension.COUNT),
    "шт.": (Decimal("1"), "unit", QuantityDimension.COUNT),
    "unit": (Decimal("1"), "unit", QuantityDimension.COUNT),
}

_FORMS = {
    "таб": "таблетка",
    "таб.": "таблетка",
    "таблетки": "таблетка",
    "таблетка": "таблетка",
    "капс": "капсула",
    "капс.": "капсула",
    "капсулы": "капсула",
    "капсула": "капсула",
    "р-р": "раствор",
    "раствор": "раствор",
    "сироп": "сироп",
    "спрей": "спрей",
}


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedConcentration:
    numerator: NormalizedQuantity
    denominator: NormalizedQuantity


class CatalogNormalizer:
    def normalize_text(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
        normalized = _PUNCTUATION.sub(" ", normalized)
        return _SPACE.sub(" ", normalized).strip(" .,-")

    def normalize_form(self, value: str) -> str:
        normalized = self.normalize_text(value)
        return _FORMS.get(normalized, normalized)

    def parse_quantity(self, raw: str) -> NormalizedQuantity:
        match = _QUANTITY.fullmatch(unicodedata.normalize("NFKC", raw))
        if match is None:
            raise NormalizationError("quantity must contain one unambiguous value and unit")
        unit_key = match.group("unit").casefold().replace("ё", "е")
        unit = _UNITS.get(unit_key)
        if unit is None:
            raise NormalizationError("quantity unit is not supported")
        try:
            value = Decimal(match.group("value").replace(",", "."))
        except InvalidOperation as error:
            raise NormalizationError("quantity value is invalid") from error
        if not value.is_finite() or value <= 0:
            raise NormalizationError("quantity value must be positive and finite")
        multiplier, canonical_unit, dimension = unit
        return NormalizedQuantity(
            value=value * multiplier,
            unit=canonical_unit,
            dimension=dimension,
            raw=raw,
        )

    def parse_concentration(self, raw: str) -> NormalizedConcentration:
        parts = raw.split("/")
        if len(parts) != 2:
            raise NormalizationError("concentration must have numerator and denominator")
        numerator = self.parse_quantity(parts[0])
        denominator = self.parse_quantity(parts[1])
        if numerator.dimension is not QuantityDimension.MASS:
            raise NormalizationError("concentration numerator must be a mass")
        if denominator.dimension not in {
            QuantityDimension.VOLUME,
            QuantityDimension.COUNT,
        }:
            raise NormalizationError("concentration denominator is incompatible")
        return NormalizedConcentration(numerator, denominator)

    def critical_signature(self, identity: ProductIdentityInput) -> str:
        values = (
            identity.kind.value,
            identity.trade_name_normalized,
            identity.active_ingredient_normalized or "",
            identity.form_normalized or "",
            self._quantity_key(identity.dosage),
            self._quantity_key(identity.concentration_numerator),
            self._quantity_key(identity.concentration_denominator),
            str(identity.package_count or ""),
            self._quantity_key(identity.volume),
            identity.route_normalized or "",
            identity.package_variant_normalized or "",
        )
        return sha256("\x1f".join(values).encode()).hexdigest()

    @staticmethod
    def _quantity_key(value: NormalizedQuantity | None) -> str:
        if value is None:
            return ""
        return f"{value.value.normalize()}:{value.unit}:{value.dimension.value}"
