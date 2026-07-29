from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pharmacy_bot.application.geocoding import (
    GeocodingService,
    ProviderCandidate,
    ProviderGeocodingResult,
    TemporaryGeocoderError,
)
from pharmacy_bot.application.geography import GeographyPolicy
from pharmacy_bot.domain.geocoding import GeocodingDecision, GeocodingPrecision
from pharmacy_bot.domain.geography import Coordinate


class Provider:
    def __init__(self, values: tuple[ProviderCandidate, ...], *, fails: bool = False) -> None:
        self.values = values
        self.fails = fails

    async def geocode(
        self, query: str, *, locale: str, region_hint: str | None
    ) -> ProviderGeocodingResult:
        del query, locale, region_hint
        if self.fails:
            raise TemporaryGeocoderError
        return ProviderGeocodingResult("test-provider", "v1", self.values)


class Repository:
    def __init__(self) -> None:
        self.saved = False

    async def save_candidates(self, *args: object, **kwargs: object) -> object:
        self.saved = True
        candidates = args[6]
        decision = args[7]
        return (
            type("Result", (), {})()
            if False
            else __import__(
                "pharmacy_bot.domain.geocoding", fromlist=["GeocodingResult"]
            ).GeocodingResult(decision, args[1], candidates, kwargs["expires_at"])
        )

    async def confirm(self, *args: object, **kwargs: object) -> object:
        raise AssertionError


def candidate(external_id: str, precision: GeocodingPrecision) -> ProviderCandidate:
    return ProviderCandidate(
        external_id,
        f"Москва, адрес {external_id}",
        Coordinate(Decimal("55.75"), Decimal("37.61")),
        precision,
    )


async def test_single_precise_candidate_is_exact_and_bounded() -> None:
    repository = Repository()
    service = GeocodingService(
        Provider((candidate("one", GeocodingPrecision.ROOFTOP),)),
        repository,  # type: ignore[arg-type]
        GeographyPolicy(),
    )

    result = await service.resolve(
        1, 2, "Москва, Тверская 1", locale="ru", region_hint="moscow", now=datetime.now(UTC)
    )

    assert result.decision is GeocodingDecision.EXACT
    assert len(result.candidates[0].candidate_id) == 24
    assert repository.saved


async def test_multiple_candidates_are_ambiguous() -> None:
    service = GeocodingService(
        Provider(
            (
                candidate("one", GeocodingPrecision.ADDRESS),
                candidate("two", GeocodingPrecision.ADDRESS),
            )
        ),
        Repository(),  # type: ignore[arg-type]
        GeographyPolicy(),
    )

    result = await service.resolve(
        1, 1, "Москва, улица", locale="ru", region_hint=None, now=datetime.now(UTC)
    )
    assert result.decision is GeocodingDecision.AMBIGUOUS


async def test_temporary_error_does_not_write_session() -> None:
    repository = Repository()
    service = GeocodingService(
        Provider((), fails=True),
        repository,  # type: ignore[arg-type]
        GeographyPolicy(),
    )

    result = await service.resolve(
        1, 1, "Москва, улица", locale="ru", region_hint=None, now=datetime.now(UTC)
    )
    assert result.decision is GeocodingDecision.TEMPORARY_ERROR
    assert not repository.saved


async def test_oversized_provider_result_is_rejected() -> None:
    service = GeocodingService(
        Provider(tuple(candidate(str(index), GeocodingPrecision.STREET) for index in range(9))),
        Repository(),  # type: ignore[arg-type]
        GeographyPolicy(),
    )

    with pytest.raises(Exception, match="too many"):
        await service.resolve(
            1, 1, "Москва, улица", locale="ru", region_hint=None, now=datetime.now(UTC)
        )
