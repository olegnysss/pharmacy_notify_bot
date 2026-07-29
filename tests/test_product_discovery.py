from __future__ import annotations

import pytest

from pharmacy_bot.domain.product_selection import (
    DiscoveryStatus,
    MatchConfidence,
)
from pharmacy_bot.infrastructure.product_discovery import (
    ConfiguredProductLinkPolicy,
    DemoProductDiscoveryGateway,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example/product/1",
        "https://user:pass@shop.example/product/1",
        "https://shop.example:8443/product/1",
        "https://127.0.0.1/product/1",
        "https://10.0.0.1/product/1",
        "https://unknown.example/product/1",
        "not a url",
    ],
)
def test_link_policy_rejects_non_https_credentials_ports_ips_and_unknown_hosts(
    url: str,
) -> None:
    policy = ConfiguredProductLinkPolicy(("shop.example",))

    assert policy.recognize(url) is None


def test_link_policy_returns_only_exact_allowlisted_host_without_fetching() -> None:
    policy = ConfiguredProductLinkPolicy(("shop.example",))

    assert policy.recognize("https://shop.example/product/1") == "shop.example"
    assert policy.recognize("https://sub.shop.example/product/1") is None


async def test_demo_gateway_is_deterministic_and_marks_ambiguity_without_network() -> None:
    gateway = DemoProductDiscoveryGateway()

    first = await gateway.search("Тестовый товар")
    second = await gateway.search("Тестовый товар")

    assert first == second
    assert first.status is DiscoveryStatus.SUCCESS
    assert len(first.candidates) == 12
    assert first.candidates[0].confidence is MatchConfidence.EXACT
    assert first.candidates[1].confidence is MatchConfidence.PROBABLE
    assert first.candidates[2].confidence is MatchConfidence.CANDIDATE
