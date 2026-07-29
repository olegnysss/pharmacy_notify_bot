from __future__ import annotations

from hashlib import sha256

from pharmacy_bot.application.subscription_setup import LocationResolution
from pharmacy_bot.domain.subscription_setup import (
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    ProductSnapshot,
    SourceOption,
)


class DefaultLocationResolver:
    """Accepts a user-entered city without sharing it with a third party."""

    async def resolve(
        self,
        mode: LocationInputMode,
        text: str,
    ) -> LocationResolution:
        if mode is not LocationInputMode.CITY:
            return LocationResolution(temporary_error=True)
        return LocationResolution(
            candidates=(
                LocationCandidate(
                    key=f"city:{sha256(text.casefold().encode()).hexdigest()[:24]}",
                    kind=LocationInputMode.CITY,
                    display_name=text,
                    city=text,
                    confidence=LocationConfidence.EXACT,
                ),
            )
        )


class DemoLocationResolver(DefaultLocationResolver):
    async def resolve(
        self,
        mode: LocationInputMode,
        text: str,
    ) -> LocationResolution:
        if mode is LocationInputMode.CITY:
            return await super().resolve(mode, text)
        if mode is LocationInputMode.ADDRESS:
            digest = sha256(text.casefold().encode()).hexdigest()[:20]
            return LocationResolution(
                candidates=(
                    LocationCandidate(
                        key=f"demo-address:{digest}:1",
                        kind=mode,
                        display_name=f"{text}, вариант 1",
                        address=f"{text}, вариант 1",
                        confidence=LocationConfidence.AMBIGUOUS,
                    ),
                    LocationCandidate(
                        key=f"demo-address:{digest}:2",
                        kind=mode,
                        display_name=f"{text}, вариант 2",
                        address=f"{text}, вариант 2",
                        confidence=LocationConfidence.AMBIGUOUS,
                    ),
                )
            )
        return LocationResolution()


class ConfiguredSourceCapabilities:
    def __init__(self, sources: tuple[SourceOption, ...]) -> None:
        self._sources = sources

    async def available_sources(
        self,
        product: ProductSnapshot,
        location: LocationCandidate,
    ) -> tuple[SourceOption, ...]:
        return self._sources


def demo_sources() -> tuple[SourceOption, ...]:
    return (
        SourceOption(
            code="demo_pharmacy",
            name="Демо-аптека",
            available=True,
            supports_price=True,
            supports_low_stock=True,
            supports_orderable=True,
        ),
        SourceOption(
            code="demo_degraded",
            name="Демо-сеть на обслуживании",
            available=False,
            unavailable_reason="Источник временно отключён и не войдёт в мониторинг.",
        ),
    )
