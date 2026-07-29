from __future__ import annotations

from decimal import Decimal

import pytest

from pharmacy_bot.application.geography import (
    GeographyPolicy,
    GeographyValidationError,
    distance_meters,
)
from pharmacy_bot.domain.geography import (
    Coordinate,
    GeographicEligibility,
    GeographicReason,
    LocationScopeInput,
    LocationScopeKind,
    PointLocation,
)


@pytest.mark.parametrize(
    "coordinate",
    [
        Coordinate(Decimal("-90"), Decimal("-180")),
        Coordinate(Decimal("90"), Decimal("180")),
        Coordinate(Decimal("55.7558"), Decimal("37.6176")),
    ],
)
def test_coordinate_boundaries_are_valid(coordinate: Coordinate) -> None:
    result = GeographyPolicy().normalize(
        LocationScopeInput(LocationScopeKind.RADIUS, coordinate=coordinate, radius_meters=1000)
    )

    assert result.coordinate is not None


@pytest.mark.parametrize(
    "coordinate",
    [
        Coordinate(Decimal("-90.000001"), Decimal("0")),
        Coordinate(Decimal("90.000001"), Decimal("0")),
        Coordinate(Decimal("0"), Decimal("-180.000001")),
        Coordinate(Decimal("0"), Decimal("180.000001")),
    ],
)
def test_invalid_coordinates_are_rejected_without_echoing_them(
    coordinate: Coordinate,
) -> None:
    with pytest.raises(GeographyValidationError) as raised:
        GeographyPolicy().normalize(
            LocationScopeInput(
                LocationScopeKind.RADIUS,
                coordinate=coordinate,
                radius_meters=1000,
            )
        )

    assert str(coordinate.latitude) not in str(raised.value)


def test_scope_shape_cannot_mix_city_and_radius() -> None:
    with pytest.raises(GeographyValidationError):
        GeographyPolicy().normalize(
            LocationScopeInput(
                LocationScopeKind.CITY,
                city_key="moscow",
                coordinate=Coordinate(Decimal("55"), Decimal("37")),
            )
        )


def test_pharmacy_list_is_sorted_deduplicated_and_bounded() -> None:
    result = GeographyPolicy(max_pharmacies=3).normalize(
        LocationScopeInput(
            LocationScopeKind.PHARMACY_LIST,
            pharmacy_ids=(3, 1, 3, 2),
        )
    )

    assert result.pharmacy_ids == (1, 2, 3)


def test_distance_is_symmetric_and_zero_for_same_point() -> None:
    moscow = Coordinate(Decimal("55.7558"), Decimal("37.6176"))
    petersburg = Coordinate(Decimal("59.9343"), Decimal("30.3351"))

    assert distance_meters(moscow, moscow) == 0
    assert distance_meters(moscow, petersburg) == distance_meters(petersburg, moscow)
    assert 630_000 < distance_meters(moscow, petersburg) < 640_000


def test_radius_boundary_is_inclusive_and_missing_coordinate_is_unknown() -> None:
    policy = GeographyPolicy(min_radius_meters=1, max_radius_meters=10_000)
    center = Coordinate(Decimal("0"), Decimal("0"))
    point = Coordinate(Decimal("0"), Decimal("0.008993"))
    distance = distance_meters(center, point)
    scope = LocationScopeInput(
        LocationScopeKind.RADIUS,
        coordinate=center,
        radius_meters=distance,
    )

    assert policy.decide(scope, PointLocation(coordinate=point)).eligibility is (
        GeographicEligibility.ELIGIBLE
    )
    missing = policy.decide(scope, PointLocation())
    assert missing.eligibility is GeographicEligibility.UNKNOWN
    assert missing.reason is GeographicReason.COORDINATE_MISSING


@pytest.mark.parametrize(
    ("scope", "point", "eligibility"),
    [
        (
            LocationScopeInput(LocationScopeKind.CITY, city_key="Москва"),
            PointLocation(city_key="москва"),
            GeographicEligibility.ELIGIBLE,
        ),
        (
            LocationScopeInput(LocationScopeKind.CITY, city_key="Москва"),
            PointLocation(city_key="Московская-область"),
            GeographicEligibility.INELIGIBLE,
        ),
        (
            LocationScopeInput(LocationScopeKind.PHARMACY_LIST, pharmacy_ids=(1, 2)),
            PointLocation(pharmacy_id=2),
            GeographicEligibility.ELIGIBLE,
        ),
        (
            LocationScopeInput(
                LocationScopeKind.ONLINE_REGION,
                online_region_key="moscow",
            ),
            PointLocation(online_region_keys=frozenset({"moscow"})),
            GeographicEligibility.ELIGIBLE,
        ),
    ],
)
def test_scope_eligibility_is_exact_and_explainable(
    scope: LocationScopeInput,
    point: PointLocation,
    eligibility: GeographicEligibility,
) -> None:
    assert GeographyPolicy().decide(scope, point).eligibility is eligibility


def test_fingerprint_is_stable_after_normalization() -> None:
    policy = GeographyPolicy()
    first = policy.normalize(LocationScopeInput(LocationScopeKind.CITY, city_key=" Москва "))
    second = policy.normalize(LocationScopeInput(LocationScopeKind.CITY, city_key="МОСКВА"))

    assert policy.fingerprint(first) == policy.fingerprint(second)
