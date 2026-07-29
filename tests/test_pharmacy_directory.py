from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from pharmacy_bot.application.geography import GeographyPolicy
from pharmacy_bot.application.pharmacy_directory import PharmacyDirectoryService
from pharmacy_bot.domain.geography import Coordinate
from pharmacy_bot.domain.pharmacy_directory import (
    PharmacyDirectoryConflict,
    PharmacyIdentity,
    PharmacyKind,
    PharmacyMatchLevel,
)


class Repository:
    pass


def identity(**changes: object) -> PharmacyIdentity:
    values: dict[str, object] = {
        "name": "Аптека №1",
        "normalized_address": "Москва, Тверская улица, 1",
        "network_key": "network-a",
        "coordinate": Coordinate(Decimal("55.757"), Decimal("37.615")),
        "kind": PharmacyKind.PHARMACY,
    }
    values.update(changes)
    return PharmacyIdentity(**values)  # type: ignore[arg-type]


def service() -> PharmacyDirectoryService:
    return PharmacyDirectoryService(Repository(), GeographyPolicy())  # type: ignore[arg-type]


def test_normalization_is_deterministic_and_preserves_coordinates() -> None:
    value = service().normalize_identity(
        identity(
            normalized_address="  МОСКВА,   ТВЕРСКАЯ улица, 1 ",
            coordinate=Coordinate(Decimal("55.7570001"), Decimal("37.6150001")),
        )
    )

    assert value.normalized_address == "москва, тверская улица, 1"
    assert value.coordinate == Coordinate(Decimal("55.757000"), Decimal("37.615000"))


def test_same_point_with_address_and_network_is_exact() -> None:
    result = service().match(
        service().normalize_identity(identity()),
        service().normalize_identity(identity(name="Другое отображаемое имя")),
    )

    assert result.level is PharmacyMatchLevel.EXACT
    assert result.score == 100
    assert result.algorithm_version == "pharmacy-match-v1"


def test_same_address_from_different_networks_is_mismatch() -> None:
    result = service().match(
        service().normalize_identity(identity(network_key="network-a")),
        service().normalize_identity(identity(network_key="network-b")),
    )

    assert result.level is PharmacyMatchLevel.MISMATCH
    assert result.reasons == ("network_mismatch",)


def test_far_points_are_never_merged_despite_same_address() -> None:
    result = service().match(
        service().normalize_identity(identity()),
        service().normalize_identity(
            identity(coordinate=Coordinate(Decimal("55.77"), Decimal("37.62")))
        ),
    )

    assert result.level is PharmacyMatchLevel.MISMATCH
    assert result.reasons == ("coordinate_mismatch",)


def test_missing_coordinate_keeps_same_address_probable() -> None:
    result = service().match(
        service().normalize_identity(identity(coordinate=None)),
        service().normalize_identity(identity()),
    )

    assert result.level is PharmacyMatchLevel.PROBABLE


def test_cursor_is_stable_and_rejects_invalid_values() -> None:
    value = service().encode_cursor(123, 456)
    assert service().decode_cursor(value) == (123, 456)

    with pytest.raises(PharmacyDirectoryConflict):
        service().decode_cursor("not-a-cursor")


def test_trusted_identifier_is_exact_unless_a_critical_gate_fails() -> None:
    trusted = service().normalize_identity(identity(trusted_identifier="official-1"))
    assert service().match(trusted, trusted).level is PharmacyMatchLevel.EXACT
    assert (
        service().match(trusted, replace(trusted, network_key="other")).level
        is PharmacyMatchLevel.MISMATCH
    )
