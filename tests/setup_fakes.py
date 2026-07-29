from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from pharmacy_bot.domain.product_selection import (
    MatchConfidence,
    ProductCandidate,
    ProductDraft,
    ProductDraftStatus,
)
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    MonitoringFilters,
    SetupStatus,
    Subscription,
    SubscriptionSetupDraft,
    SubscriptionStatus,
)


class ConfirmedProductDraftReader:
    def __init__(self, user_id: int = 1) -> None:
        candidate = ProductCandidate(
            candidate_key="product-1",
            version="product-v1",
            name="Тестовый товар",
            form="таблетки",
            dosage="10 мг",
            package="№20",
            manufacturer="Производитель",
            source_name="Каталог",
            source_host="demo.pharmacy.local",
            confidence=MatchConfidence.EXACT,
            ordinal=0,
        )
        self.draft = ProductDraft(
            id=1,
            user_id=user_id,
            generation=3,
            status=ProductDraftStatus.CONFIRMED,
            input_mode=None,
            query_text="Тестовый товар",
            source_host=None,
            candidates=(candidate,),
            selected_ordinal=0,
            selected_candidate_version="product-v1",
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )

    async def get(self, user_id: int) -> ProductDraft | None:
        return replace(self.draft, user_id=user_id)


class InMemorySetupRepository:
    def __init__(self) -> None:
        self.drafts: dict[int, SubscriptionSetupDraft] = {}
        self.subscriptions: dict[str, Subscription] = {}
        self.creation_count = 0

    async def start_or_resume(
        self,
        user_id: int,
        product,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> SubscriptionSetupDraft:
        existing = self.drafts.get(user_id)
        if (
            existing
            and existing.status not in {SetupStatus.CREATED, SetupStatus.CANCELLED}
            and existing.expires_at > now
            and existing.product.candidate_key == product.candidate_key
            and existing.product.version == product.version
        ):
            return existing
        generation = existing.generation + 1 if existing else 1
        draft = SubscriptionSetupDraft(
            id=user_id,
            user_id=user_id,
            generation=generation,
            status=SetupStatus.CHOOSE_LOCATION,
            product=product,
            location_mode=None,
            location_candidates=(),
            location=None,
            radius_meters=None,
            available_sources=(),
            selected_source_codes=(),
            filters=MonitoringFilters(),
            completion_mode=None,
            ends_at=None,
            idempotency_key=f"setup-{user_id}-{generation}",
            expires_at=expires_at,
        )
        self.drafts[user_id] = draft
        return draft

    async def get(self, user_id: int) -> SubscriptionSetupDraft | None:
        return self.drafts.get(user_id)

    async def save(
        self,
        draft: SubscriptionSetupDraft,
        *,
        expected_generation: int,
    ) -> SubscriptionSetupDraft | None:
        current = self.drafts.get(draft.user_id)
        if current is None or current.generation != expected_generation:
            return None
        saved = replace(draft, generation=expected_generation + 1)
        self.drafts[draft.user_id] = saved
        return saved

    async def create_subscription(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
        max_active_subscriptions: int = 20,
    ) -> tuple[SubscriptionSetupDraft, Subscription] | None:
        draft = self.drafts.get(user_id)
        if draft is None:
            return None
        existing = self.subscriptions.get(draft.idempotency_key)
        if existing:
            return draft, existing
        if (
            draft.generation != expected_generation
            or draft.status is not SetupStatus.REVIEW
            or draft.location is None
            or draft.radius_meters is None
            or draft.completion_mode is None
        ):
            return None
        self.creation_count += 1
        subscription = Subscription(
            id=self.creation_count,
            user_id=user_id,
            product=draft.product,
            location=draft.location,
            radius_meters=draft.radius_meters,
            source_codes=draft.selected_source_codes,
            filters=draft.filters,
            completion_mode=draft.completion_mode,
            ends_at=draft.ends_at,
            status=SubscriptionStatus.ACTIVE,
            availability_state=AvailabilityState.PENDING,
            created_at=now,
        )
        created = replace(
            draft,
            generation=draft.generation + 1,
            status=SetupStatus.CREATED,
            subscription_id=subscription.id,
        )
        self.drafts[user_id] = created
        self.subscriptions[draft.idempotency_key] = subscription
        return created, subscription
