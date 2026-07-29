from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pharmacy_bot.application.catalog_normalization import CatalogNormalizer
from pharmacy_bot.application.source_revalidation import (
    SourceDriftClassifier,
    aggregate_subscription_state,
)
from pharmacy_bot.domain.source_product import SourceProductAttributes
from pharmacy_bot.domain.source_revalidation import (
    DriftClass,
    MonitoringEligibility,
    OfferAvailability,
    SourceVersionIdentity,
    SubscriptionAggregateState,
)


def version(
    number: int,
    *,
    raw_name: str = "Тест 10 мг",
    url: str = "https://pharmacy.example/p/1",
    **attribute_changes: object,
) -> SourceVersionIdentity:
    attributes: dict[str, object] = {
        "kind": "medicine",
        "manufacturer": "производитель",
        "form": "таблетка",
        "dosage": "10 mg",
        "package_count": 20,
    }
    attributes.update(attribute_changes)
    return SourceVersionIdentity(
        source_product_id=1,
        source_version=number,
        observed_at=datetime(2026, 7, 29, 20, number, tzinfo=UTC),
        canonical_url=url,
        raw_name=raw_name,
        attributes=SourceProductAttributes(**attributes),  # type: ignore[arg-type]
        semantic_fingerprint=str(number) * 64,
    )


@pytest.mark.parametrize(
    ("current", "expected", "critical_field"),
    [
        (version(2, dosage="100 mg"), DriftClass.CRITICAL, "dosage"),
        (version(2, form="раствор"), DriftClass.CRITICAL, "form"),
        (version(2, package_count=100), DriftClass.CRITICAL, "package_count"),
        (version(2, dosage=None), DriftClass.INCOMPLETE, None),
        (
            version(2, raw_name="  ТЕСТ   10 мг  "),
            DriftClass.NONE,
            None,
        ),
        (
            version(2, url="https://pharmacy.example/new/1"),
            DriftClass.COSMETIC,
            None,
        ),
    ],
)
def test_drift_classifier_is_deterministic_and_preserves_critical_differences(
    current: SourceVersionIdentity,
    expected: DriftClass,
    critical_field: str | None,
) -> None:
    result = SourceDriftClassifier(CatalogNormalizer()).classify(version(1), current)

    assert result.drift_class is expected
    assert result.algorithm_version == "source-drift-v1"
    if critical_field:
        assert critical_field in result.evidence.critical_fields


def test_quarantined_offer_does_not_hide_an_eligible_available_offer() -> None:
    result = aggregate_subscription_state(
        (
            OfferAvailability(1, MonitoringEligibility.QUARANTINED, True),
            OfferAvailability(2, MonitoringEligibility.ELIGIBLE, True),
        )
    )

    assert result is SubscriptionAggregateState.AVAILABLE


def test_only_quarantined_offers_require_clarification() -> None:
    result = aggregate_subscription_state(
        (
            OfferAvailability(1, MonitoringEligibility.QUARANTINED, True),
            OfferAvailability(2, MonitoringEligibility.PENDING_REVALIDATION, None),
        )
    )

    assert result is SubscriptionAggregateState.REQUIRES_CLARIFICATION


def test_no_offers_is_unknown_and_eligible_absence_is_unavailable() -> None:
    assert aggregate_subscription_state(()) is SubscriptionAggregateState.UNKNOWN
    assert (
        aggregate_subscription_state((OfferAvailability(1, MonitoringEligibility.ELIGIBLE, False),))
        is SubscriptionAggregateState.UNAVAILABLE
    )
