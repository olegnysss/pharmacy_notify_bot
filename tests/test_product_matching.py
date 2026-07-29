from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from pharmacy_bot.application.product_matching import (
    MappingActor,
    MappingConfirmation,
    MappingDecisionService,
    MatchingThresholds,
    ProductMatchingEngine,
)
from pharmacy_bot.domain.product_matching import (
    MappingActorType,
    MappingAuthorizationError,
    MappingScope,
    MatchIdentity,
    MatchLevel,
    MatchReason,
    MatchRequest,
)


def identity(**changes: object) -> MatchIdentity:
    values: dict[str, object] = {
        "kind": "medicine",
        "trade_name": "тест",
        "active_ingredient": "вещество",
        "manufacturer": "производитель",
        "form": "таблетка",
        "dosage": "10 mg",
        "package_count": 20,
        "route": "oral",
        "trusted_identifiers": frozenset({("registration", "reg-123")}),
    }
    values.update(changes)
    return MatchIdentity(**values)  # type: ignore[arg-type]


def request(
    *,
    source: MatchIdentity | None = None,
    canonical: MatchIdentity | None = None,
) -> MatchRequest:
    return MatchRequest(
        source_product_id=10,
        source_product_version=2,
        source_code="source-a",
        source=source or identity(),
        canonical_product_id=20,
        canonical_product_version=3,
        canonical=canonical or identity(),
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("dosage", "100 mg", MatchReason.DOSAGE_MISMATCH),
        ("form", "раствор", MatchReason.FORM_MISMATCH),
        ("package_count", 100, MatchReason.PACKAGE_COUNT_MISMATCH),
        ("route", "intravenous", MatchReason.ROUTE_MISMATCH),
        ("manufacturer", "другой", MatchReason.MANUFACTURER_MISMATCH),
    ],
)
def test_critical_mismatch_blocks_even_a_shared_trusted_identifier(
    field: str,
    value: object,
    reason: MatchReason,
) -> None:
    result = ProductMatchingEngine().evaluate(request(source=replace(identity(), **{field: value})))

    assert result.level is MatchLevel.MISMATCH
    assert reason in result.reasons
    assert not result.auto_event_allowed


def test_trusted_identifier_produces_explainable_exact_match() -> None:
    result = ProductMatchingEngine().evaluate(
        request(source=replace(identity(), manufacturer=None))
    )

    assert result.level is MatchLevel.EXACT
    assert result.reasons == (MatchReason.TRUSTED_IDENTIFIER,)
    assert result.algorithm_version == "critical-gate-v1"
    assert "manufacturer" in result.evidence.missing_features
    assert result.auto_event_allowed


def test_full_signature_is_exact_without_a_shared_identifier() -> None:
    source = replace(identity(), trusted_identifiers=frozenset())
    canonical = replace(identity(), trusted_identifiers=frozenset())

    result = ProductMatchingEngine().evaluate(request(source=source, canonical=canonical))

    assert result.level is MatchLevel.EXACT
    assert result.reasons == (MatchReason.CRITICAL_SIGNATURE,)


def test_missing_manufacturer_lowers_result_to_probable_without_auto_event() -> None:
    source = replace(identity(), manufacturer=None, trusted_identifiers=frozenset())
    canonical = replace(identity(), trusted_identifiers=frozenset())

    result = ProductMatchingEngine().evaluate(request(source=source, canonical=canonical))

    assert result.level is MatchLevel.PROBABLE
    assert result.score == 74
    assert "manufacturer" in result.evidence.missing_features
    assert not result.auto_event_allowed
    assert result.distinguishing_features == (
        "form: таблетка",
        "dosage: 10 mg",
        "package: 20",
        "manufacturer: производитель",
    )


def test_candidate_and_low_similarity_mismatch_are_deterministic() -> None:
    engine = ProductMatchingEngine(MatchingThresholds(probable_score=70, candidate_score=40))
    candidate = engine.evaluate(
        request(
            source=MatchIdentity(
                kind="medicine",
                trade_name="тест",
                active_ingredient="вещество",
                manufacturer=None,
            ),
            canonical=replace(identity(), trusted_identifiers=frozenset()),
        )
    )
    unrelated = engine.evaluate(
        request(
            source=MatchIdentity(kind="medicine", trade_name=""),
            canonical=replace(identity(), trusted_identifiers=frozenset()),
        )
    )
    ranked = engine.rank(((30, candidate), (10, candidate), (5, unrelated)))

    assert candidate.level is MatchLevel.CANDIDATE
    assert unrelated.level is MatchLevel.MISMATCH
    assert unrelated.reasons == (MatchReason.INSUFFICIENT_SIMILARITY,)
    assert [item[0] for item in ranked] == [10, 30, 5]


@pytest.mark.parametrize(
    ("candidate", "probable"),
    [(50, 50), (-1, 70), (30, 100), (80, 70)],
)
def test_invalid_thresholds_are_rejected(candidate: int, probable: int) -> None:
    with pytest.raises(ValueError):
        MatchingThresholds(probable_score=probable, candidate_score=candidate)


class DecisionRepositorySpy:
    def __init__(self) -> None:
        self.created = False
        self.active = False

    async def create_or_get(
        self,
        confirmation: MappingConfirmation,
        *,
        now: datetime,
    ) -> object:
        del confirmation, now
        self.created = True
        return object()

    async def revoke(
        self,
        decision_id: int,
        expected_version: int,
        actor: MappingActor,
        *,
        now: datetime,
    ) -> object:
        del decision_id, expected_version, actor, now
        return object()

    async def active_rule_exists(
        self,
        source_product_id: int,
        canonical_product_id: int,
        *,
        source_code: str,
        user_id: int | None,
    ) -> bool:
        del source_product_id, canonical_product_id, source_code, user_id
        return self.active


async def test_user_can_only_confirm_for_own_scope() -> None:
    repository = DecisionRepositorySpy()
    service = MappingDecisionService(repository)  # type: ignore[arg-type]
    result = ProductMatchingEngine().evaluate(
        request(
            source=replace(identity(), manufacturer=None, trusted_identifiers=frozenset()),
            canonical=replace(identity(), trusted_identifiers=frozenset()),
        )
    )
    base = MappingConfirmation(
        request(),
        result,
        MappingActor(MappingActorType.USER, 42),
        MappingScope.GLOBAL,
        None,
        "user_confirmed",
        "update-12345678",
    )

    with pytest.raises(MappingAuthorizationError):
        await service.confirm(base, now=datetime.now(UTC))
    decision = await service.confirm(
        replace(base, scope=MappingScope.USER, scope_user_id=42),
        now=datetime.now(UTC),
    )

    assert decision is not None
    assert repository.created


async def test_probable_requires_active_scoped_rule_for_auto_event() -> None:
    repository = DecisionRepositorySpy()
    service = MappingDecisionService(repository)  # type: ignore[arg-type]
    result = ProductMatchingEngine().evaluate(
        request(
            source=replace(identity(), manufacturer=None, trusted_identifiers=frozenset()),
            canonical=replace(identity(), trusted_identifiers=frozenset()),
        )
    )

    assert not await service.auto_event_allowed(request(), result, user_id=42)
    repository.active = True
    assert await service.auto_event_allowed(request(), result, user_id=42)
