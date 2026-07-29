from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from typing import Protocol

from pharmacy_bot.application.geography import GeographyPolicy
from pharmacy_bot.domain.fulfillment import (
    FulfillmentDecision,
    FulfillmentInput,
    FulfillmentPresentation,
    FulfillmentRecord,
    FulfillmentType,
    FulfillmentValidationError,
)
from pharmacy_bot.domain.geography import (
    GeographicEligibility,
    LocationScopeInput,
    LocationScopeKind,
    PointLocation,
)

_KEY = re.compile(r"^[\w.-]{1,128}$", re.UNICODE)


class FulfillmentRepository(Protocol):
    async def upsert(
        self,
        source_product_id: int,
        value: FulfillmentInput,
        reference_key: str,
        fingerprint: str,
        *,
        now: datetime,
    ) -> FulfillmentRecord: ...


class FulfillmentService:
    def __init__(self, repository: FulfillmentRepository, geography: GeographyPolicy) -> None:
        self._repository = repository
        self._geography = geography

    def validate(self, raw: FulfillmentInput) -> FulfillmentInput:
        source_code = self._key(raw.source_code)
        region = self._key(raw.delivery_region_key) if raw.delivery_region_key else None
        city = self._key(raw.delivery_city_key) if raw.delivery_city_key else None
        coordinate = self._geography._coordinate(raw.coordinate) if raw.coordinate else None
        value = FulfillmentInput(
            raw.fulfillment_type,
            source_code,
            raw.pharmacy_id,
            coordinate,
            region,
            city,
        )
        if raw.fulfillment_type in {
            FulfillmentType.PHYSICAL_STOCK,
            FulfillmentType.PICKUP,
        }:
            valid = (
                value.pharmacy_id is not None
                and value.pharmacy_id > 0
                and value.coordinate is not None
                and region is None
                and city is None
            )
        elif raw.fulfillment_type is FulfillmentType.DELIVERY:
            valid = (
                value.pharmacy_id is None
                and value.coordinate is None
                and (region is not None or city is not None)
            )
        else:
            valid = (
                value.pharmacy_id is None
                and value.coordinate is None
                and region is None
                and city is None
            )
        if not valid:
            raise FulfillmentValidationError("fulfillment references do not match fulfillment type")
        return value

    async def upsert(
        self,
        source_product_id: int,
        raw: FulfillmentInput,
        *,
        now: datetime,
    ) -> FulfillmentRecord:
        if source_product_id <= 0:
            raise FulfillmentValidationError("source product reference is invalid")
        value = self.validate(raw)
        reference_key = self.reference_key(value)
        payload = asdict(value)
        payload["fulfillment_type"] = value.fulfillment_type.value
        if value.coordinate:
            payload["coordinate"] = {
                "latitude": str(value.coordinate.latitude),
                "longitude": str(value.coordinate.longitude),
            }
        fingerprint = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return await self._repository.upsert(
            source_product_id,
            value,
            reference_key,
            fingerprint,
            now=now,
        )

    def applicable(
        self,
        value: FulfillmentInput,
        scope: LocationScopeInput,
    ) -> FulfillmentDecision:
        value = self.validate(value)
        if value.fulfillment_type is FulfillmentType.ONLINE_UNKNOWN:
            return FulfillmentDecision(
                GeographicEligibility.UNKNOWN,
                "online_coverage_unknown",
            )
        if value.fulfillment_type in {
            FulfillmentType.PHYSICAL_STOCK,
            FulfillmentType.PICKUP,
        }:
            result = self._geography.decide(
                scope,
                PointLocation(
                    coordinate=value.coordinate,
                    pharmacy_id=value.pharmacy_id,
                ),
            )
            return FulfillmentDecision(
                result.eligibility,
                (f"{value.fulfillment_type.value}:{result.reason.value}"),
                result.distance_meters,
            )
        if scope.kind is LocationScopeKind.ONLINE_REGION:
            matches = scope.online_region_key in {
                value.delivery_region_key,
                value.delivery_city_key,
            }
        elif scope.kind is LocationScopeKind.REGION:
            matches = scope.region_key == value.delivery_region_key
        elif scope.kind is LocationScopeKind.CITY:
            matches = scope.city_key == value.delivery_city_key
        else:
            return FulfillmentDecision(
                GeographicEligibility.INELIGIBLE,
                "delivery_not_applicable_to_physical_scope",
            )
        return FulfillmentDecision(
            GeographicEligibility.ELIGIBLE if matches else GeographicEligibility.INELIGIBLE,
            "delivery_zone_match" if matches else "delivery_zone_mismatch",
        )

    @staticmethod
    def presentation(value: FulfillmentInput) -> FulfillmentPresentation:
        if value.fulfillment_type is FulfillmentType.PHYSICAL_STOCK:
            return FulfillmentPresentation(
                "В наличии в аптеке",
                "Источник подтверждает остаток в указанной физической точке.",
                True,
            )
        if value.fulfillment_type is FulfillmentType.PICKUP:
            return FulfillmentPresentation(
                "Доступно для самовывоза",
                "Самовывоз не означает подтверждённый остаток в пункте.",
                False,
            )
        if value.fulfillment_type is FulfillmentType.DELIVERY:
            return FulfillmentPresentation(
                "Доставка в выбранный регион",
                "Это интернет-предложение, а не остаток в конкретной аптеке.",
                False,
            )
        return FulfillmentPresentation(
            "Доступность в интернете требует уточнения",
            "Источник не указал точку, самовывоз или зону доставки.",
            False,
        )

    @staticmethod
    def reference_key(value: FulfillmentInput) -> str:
        if value.fulfillment_type in {
            FulfillmentType.PHYSICAL_STOCK,
            FulfillmentType.PICKUP,
        }:
            return f"pharmacy:{value.pharmacy_id}"
        if value.fulfillment_type is FulfillmentType.DELIVERY:
            return f"delivery:{value.delivery_region_key or ''}:{value.delivery_city_key or ''}"
        return "online:unknown"

    @staticmethod
    def _key(value: str | None) -> str:
        normalized = (value or "").strip().casefold()
        if not _KEY.fullmatch(normalized):
            raise FulfillmentValidationError("fulfillment key is invalid")
        return normalized
