from __future__ import annotations

from decimal import Decimal

import pytest

from pharmacy_bot.application.fulfillment import FulfillmentService
from pharmacy_bot.application.geography import GeographyPolicy
from pharmacy_bot.domain.fulfillment import (
    FulfillmentInput,
    FulfillmentType,
    FulfillmentValidationError,
)
from pharmacy_bot.domain.geography import (
    Coordinate,
    GeographicEligibility,
    LocationScopeInput,
    LocationScopeKind,
)


class Repository:
    pass


def service() -> FulfillmentService:
    return FulfillmentService(Repository(), GeographyPolicy())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        FulfillmentInput(FulfillmentType.PHYSICAL_STOCK, "source"),
        FulfillmentInput(
            FulfillmentType.PICKUP,
            "source",
            pharmacy_id=1,
            coordinate=None,
        ),
        FulfillmentInput(
            FulfillmentType.DELIVERY,
            "source",
            pharmacy_id=1,
            delivery_region_key="moscow",
        ),
        FulfillmentInput(
            FulfillmentType.ONLINE_UNKNOWN,
            "source",
            delivery_city_key="moscow",
        ),
    ],
)
def test_invalid_type_reference_combinations_are_rejected(
    value: FulfillmentInput,
) -> None:
    with pytest.raises(FulfillmentValidationError):
        service().validate(value)


def test_physical_stock_and_pickup_use_point_geography_but_different_claims() -> None:
    coordinate = Coordinate(Decimal("55.75"), Decimal("37.61"))
    scope = LocationScopeInput(
        LocationScopeKind.RADIUS,
        coordinate=coordinate,
        radius_meters=1000,
    )
    physical = FulfillmentInput(
        FulfillmentType.PHYSICAL_STOCK,
        "source",
        pharmacy_id=1,
        coordinate=coordinate,
    )
    pickup = FulfillmentInput(
        FulfillmentType.PICKUP,
        "source",
        pharmacy_id=1,
        coordinate=coordinate,
    )

    assert service().applicable(physical, scope).eligibility is GeographicEligibility.ELIGIBLE
    assert service().applicable(pickup, scope).eligibility is GeographicEligibility.ELIGIBLE
    assert service().presentation(physical).claims_physical_stock
    assert not service().presentation(pickup).claims_physical_stock
    assert "не означает" in service().presentation(pickup).detail


def test_delivery_matches_canonical_city_but_not_radius_or_pharmacy_list() -> None:
    delivery = FulfillmentInput(
        FulfillmentType.DELIVERY,
        "source",
        delivery_region_key="moscow-region",
        delivery_city_key="moscow",
    )

    assert (
        service()
        .applicable(
            delivery,
            LocationScopeInput(LocationScopeKind.CITY, city_key="moscow"),
        )
        .eligibility
        is GeographicEligibility.ELIGIBLE
    )
    assert (
        service()
        .applicable(
            delivery,
            LocationScopeInput(
                LocationScopeKind.RADIUS,
                coordinate=Coordinate(Decimal("55"), Decimal("37")),
                radius_meters=1000,
            ),
        )
        .eligibility
        is GeographicEligibility.INELIGIBLE
    )
    assert not service().presentation(delivery).claims_physical_stock
    assert "не остаток" in service().presentation(delivery).detail


def test_online_unknown_never_becomes_available() -> None:
    value = FulfillmentInput(FulfillmentType.ONLINE_UNKNOWN, "source")
    decision = service().applicable(
        value,
        LocationScopeInput(LocationScopeKind.ONLINE_REGION, online_region_key="moscow"),
    )

    assert decision.eligibility is GeographicEligibility.UNKNOWN
    assert not service().presentation(value).claims_physical_stock


def test_reference_keys_are_explicit_and_nonnullable() -> None:
    coordinate = Coordinate(Decimal("55"), Decimal("37"))
    assert (
        service().reference_key(
            FulfillmentInput(
                FulfillmentType.PHYSICAL_STOCK,
                "source",
                pharmacy_id=10,
                coordinate=coordinate,
            )
        )
        == "pharmacy:10"
    )
    assert (
        service().reference_key(
            FulfillmentInput(
                FulfillmentType.DELIVERY,
                "source",
                delivery_region_key="region",
            )
        )
        == "delivery:region:"
    )
    assert (
        service().reference_key(FulfillmentInput(FulfillmentType.ONLINE_UNKNOWN, "source"))
        == "online:unknown"
    )
