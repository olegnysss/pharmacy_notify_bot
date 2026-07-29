from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pharmacy_bot.domain.geography import Coordinate, GeographicEligibility


class FulfillmentType(StrEnum):
    PHYSICAL_STOCK = "physical_stock"
    PICKUP = "pickup"
    DELIVERY = "delivery"
    ONLINE_UNKNOWN = "online_unknown"


@dataclass(frozen=True, slots=True)
class FulfillmentInput:
    fulfillment_type: FulfillmentType
    source_code: str
    pharmacy_id: int | None = None
    coordinate: Coordinate | None = None
    delivery_region_key: str | None = None
    delivery_city_key: str | None = None


@dataclass(frozen=True, slots=True)
class FulfillmentRecord:
    id: int
    source_product_id: int
    value: FulfillmentInput
    reference_key: str
    fingerprint: str
    version: int
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class FulfillmentDecision:
    eligibility: GeographicEligibility
    reason: str
    distance_meters: int | None = None


@dataclass(frozen=True, slots=True)
class FulfillmentPresentation:
    label: str
    detail: str
    claims_physical_stock: bool


class FulfillmentValidationError(Exception):
    pass
