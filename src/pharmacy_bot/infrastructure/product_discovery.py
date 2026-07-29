from __future__ import annotations

import ipaddress
from hashlib import sha256
from urllib.parse import urlsplit

from pharmacy_bot.domain.product_selection import (
    DiscoveryResponse,
    DiscoveryStatus,
    MatchConfidence,
    ProductCandidate,
)


class ConfiguredProductLinkPolicy:
    def __init__(self, supported_hosts: tuple[str, ...]) -> None:
        self._supported_hosts = frozenset(host.lower().rstrip(".") for host in supported_hosts)

    def recognize(self, url: str) -> str | None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.hostname is None
        ):
            return None

        host = parsed.hostname.lower().rstrip(".")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        return host if host in self._supported_hosts else None


class UnavailableProductDiscoveryGateway:
    async def search(self, query: str) -> DiscoveryResponse:
        return DiscoveryResponse(status=DiscoveryStatus.TEMPORARY_ERROR)

    async def resolve_link(self, source_host: str, url: str) -> DiscoveryResponse:
        return DiscoveryResponse(status=DiscoveryStatus.TEMPORARY_ERROR)


class DemoProductDiscoveryGateway:
    """Deterministic local UX fixture. It performs no network requests."""

    async def search(self, query: str) -> DiscoveryResponse:
        normalized = " ".join(query.split())
        candidates = tuple(
            ProductCandidate(
                candidate_key=self._key(normalized, index),
                version="demo-v1",
                name=f"{normalized} — демонстрационный вариант {index + 1}",
                form="таблетки" if index % 2 == 0 else "капсулы",
                dosage=f"{10 + index * 5} мг",
                package=f"№{10 + index * 10}",
                manufacturer=f"Демо-производитель {index + 1}",
                source_name="Локальный демо-каталог",
                source_host="demo.pharmacy.local",
                confidence=(
                    MatchConfidence.EXACT
                    if index == 0
                    else (MatchConfidence.PROBABLE if index == 1 else MatchConfidence.CANDIDATE)
                ),
            )
            for index in range(12)
        )
        return DiscoveryResponse(
            status=DiscoveryStatus.SUCCESS,
            candidates=candidates,
        )

    async def resolve_link(self, source_host: str, url: str) -> DiscoveryResponse:
        return DiscoveryResponse(
            status=DiscoveryStatus.SUCCESS,
            candidates=(
                ProductCandidate(
                    candidate_key=self._key(url, 0),
                    version="demo-link-v1",
                    name="Демонстрационный товар из ссылки",
                    form="таблетки",
                    dosage="10 мг",
                    package="№20",
                    manufacturer="Демо-производитель",
                    source_name="Локальный демо-каталог",
                    source_host=source_host,
                    confidence=MatchConfidence.CANDIDATE,
                ),
            ),
        )

    @staticmethod
    def _key(value: str, index: int) -> str:
        digest = sha256(f"{value}:{index}".encode()).hexdigest()[:24]
        return f"demo-{digest}"
