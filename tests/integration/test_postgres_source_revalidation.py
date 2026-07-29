from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from pharmacy_bot.application.catalog_normalization import CatalogNormalizer
from pharmacy_bot.application.source_revalidation import (
    SourceDriftClassifier,
    SourceRevalidationService,
)
from pharmacy_bot.domain.product_matching import MatchLevel
from pharmacy_bot.domain.source_product import SourceProductAttributes
from pharmacy_bot.domain.source_revalidation import (
    MonitoringEligibility,
    RevalidationActor,
    RevalidationAuthorizationError,
    SourceVersionIdentity,
)
from pharmacy_bot.infrastructure.database import create_engine, create_session_factory
from pharmacy_bot.infrastructure.models import (
    MappingDecisionModel,
    SourceProductModel,
    SourceProductRevalidationModel,
    SourceProductVersionModel,
)
from pharmacy_bot.infrastructure.source_revalidation_repository import (
    SqlAlchemySourceRevalidationRepository,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


def identity(
    source_id: int,
    version: int,
    *,
    dosage: str,
    observed_at: datetime,
) -> SourceVersionIdentity:
    return SourceVersionIdentity(
        source_id,
        version,
        observed_at,
        "https://pharmacy.example/p/1",
        f"Тест {dosage}",
        SourceProductAttributes(
            kind="medicine",
            manufacturer="производитель",
            form="таблетка",
            dosage=dosage,
            package_count=20,
        ),
        str(version) * 64,
    )


async def test_critical_drift_quarantines_and_delivery_requires_fresh_recovery(
    database_url: str,
) -> None:
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemySourceRevalidationRepository(session_factory)
    service = SourceRevalidationService(repository)
    classifier = SourceDriftClassifier(CatalogNormalizer())
    now = datetime(2026, 7, 29, 23, 0, tzinfo=UTC)
    try:
        async with session_factory.begin() as session:
            await session.execute(delete(SourceProductRevalidationModel))
            await session.execute(delete(MappingDecisionModel))
            await session.execute(delete(SourceProductVersionModel))
            await session.execute(delete(SourceProductModel))
            source = SourceProductModel(
                source_code="source-a",
                external_id="external-drift",
                canonical_url="https://pharmacy.example/p/1",
                raw_name="Тест 100 мг",
                parsed_attributes={"kind": "medicine", "dosage": "100 mg"},
                status="active",
                semantic_fingerprint="2" * 64,
                search_document="тест | 100 mg",
                version=2,
                monitoring_eligibility="pending_revalidation",
                last_revalidated_version=1,
                fresh_check_required=False,
                first_seen_at=now - timedelta(hours=1),
                last_seen_at=now,
                updated_at=now,
            )
            session.add(source)
            await session.flush()
            source_id = source.id

        previous = identity(
            source_id,
            1,
            dosage="10 mg",
            observed_at=now - timedelta(hours=1),
        )
        current = identity(source_id, 2, dosage="100 mg", observed_at=now)
        drift = classifier.classify(previous, current)
        quarantined = await service.revalidate(
            previous,
            current,
            drift,
            MatchLevel.MISMATCH,
            "critical-gate-v1",
            now=now,
        )
        repeated = await service.revalidate(
            previous,
            current,
            drift,
            MatchLevel.MISMATCH,
            "critical-gate-v1",
            now=now + timedelta(seconds=1),
        )
        assert not await service.delivery_eligible(source_id, 1)
        assert not await service.delivery_eligible(source_id, 2)
        with pytest.raises(RevalidationAuthorizationError):
            await service.release(
                source_id,
                2,
                RevalidationActor("operator", 7, frozenset({"catalog_mapping"})),
                exact_or_confirmed=False,
                reason="manual",
                now=now,
            )
        released = await service.release(
            source_id,
            2,
            RevalidationActor("operator", 7, frozenset({"catalog_mapping"})),
            exact_or_confirmed=True,
            reason="operator_confirmed_mapping",
            now=now + timedelta(minutes=1),
        )
        assert not await service.delivery_eligible(source_id, 2)
        restored = await service.accept_fresh_check(
            source_id,
            2,
            exact_or_confirmed=True,
            now=now + timedelta(minutes=2),
        )
        assert await service.delivery_eligible(source_id, 2)
        async with session_factory() as session:
            audit_count = await session.scalar(
                select(func.count()).select_from(SourceProductRevalidationModel)
            )
            release_audit = await session.scalar(
                select(SourceProductRevalidationModel).where(
                    SourceProductRevalidationModel.algorithm_version == "manual-release-v1"
                )
            )

        assert quarantined.eligibility is MonitoringEligibility.QUARANTINED
        assert repeated.eligibility is MonitoringEligibility.QUARANTINED
        assert released.eligibility is MonitoringEligibility.AWAITING_FRESH_CHECK
        assert restored.eligibility is MonitoringEligibility.ELIGIBLE
        assert audit_count == 3
        assert release_audit
        assert release_audit.actor_internal_id == 7
        assert release_audit.safe_evidence == {}
    finally:
        await engine.dispose()
