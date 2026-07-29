from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from pharmacy_bot.domain.subscription_lifecycle import (
    EditStatus,
    LifecycleAction,
    LifecycleTransition,
    SubscriptionEditDraft,
)
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    SourceOption,
    Subscription,
    SubscriptionStatus,
)


class InMemoryLifecycleRepository:
    def __init__(self, subscription: Subscription) -> None:
        self.subscription = subscription
        self.draft: SubscriptionEditDraft | None = None
        self.audit: list[tuple[str, int]] = []

    async def get_owned_including_deleted(
        self,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None:
        return (
            self.subscription
            if self.subscription.user_id == user_id and self.subscription.id == subscription_id
            else None
        )

    async def start_edit(
        self,
        user_id: int,
        subscription: Subscription,
        sources: tuple[SourceOption, ...],
        *,
        now: datetime,
        expires_at: datetime,
    ) -> SubscriptionEditDraft:
        if (
            self.draft
            and self.draft.subscription_id == subscription.id
            and self.draft.status not in {EditStatus.APPLIED, EditStatus.CANCELLED}
            and self.draft.expires_at > now
        ):
            return self.draft
        generation = self.draft.generation + 1 if self.draft else 1
        self.draft = SubscriptionEditDraft(
            id=1,
            user_id=user_id,
            subscription_id=subscription.id,
            generation=generation,
            status=EditStatus.CHOOSE_BLOCK,
            base_updated_at=subscription.updated_at or subscription.created_at,
            original=subscription,
            location_mode=None,
            location_candidates=(),
            location=subscription.location,
            radius_meters=subscription.radius_meters,
            available_sources=tuple(
                replace(item, ordinal=index) for index, item in enumerate(sources)
            ),
            selected_source_codes=subscription.source_codes,
            filters=subscription.filters,
            completion_mode=subscription.completion_mode,
            ends_at=subscription.ends_at,
            idempotency_key=f"edit-{generation}",
            expires_at=expires_at,
        )
        return self.draft

    async def get_edit(self, user_id: int) -> SubscriptionEditDraft | None:
        return self.draft if self.draft and self.draft.user_id == user_id else None

    async def save_edit(
        self,
        draft: SubscriptionEditDraft,
        *,
        expected_generation: int,
    ) -> SubscriptionEditDraft | None:
        if self.draft is None or self.draft.generation != expected_generation:
            return None
        self.draft = replace(draft, generation=expected_generation + 1)
        return self.draft

    async def apply_edit(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
    ) -> tuple[SubscriptionEditDraft, LifecycleTransition] | None:
        if self.draft is None:
            return None
        if self.draft.status is EditStatus.APPLIED:
            return self.draft, LifecycleTransition(
                LifecycleAction.ALREADY_APPLIED,
                self.subscription,
            )
        if (
            self.draft.generation != expected_generation
            or self.draft.status is not EditStatus.REVIEW
            or self.draft.base_updated_at
            != (self.subscription.updated_at or self.subscription.created_at)
        ):
            return self.draft, LifecycleTransition(
                LifecycleAction.STALE,
                self.subscription,
            )
        scope_changed = (
            self.draft.location.key != self.subscription.location.key
            or self.draft.radius_meters != self.subscription.radius_meters
            or set(self.draft.selected_source_codes) != set(self.subscription.source_codes)
        )
        self.subscription = replace(
            self.subscription,
            location=self.draft.location,
            radius_meters=self.draft.radius_meters,
            source_codes=self.draft.selected_source_codes,
            filters=self.draft.filters,
            completion_mode=self.draft.completion_mode,
            ends_at=self.draft.ends_at,
            availability_state=(
                AvailabilityState.UNKNOWN if scope_changed else self.subscription.availability_state
            ),
            updated_at=now,
        )
        self.draft = replace(
            self.draft,
            generation=self.draft.generation + 1,
            status=EditStatus.APPLIED,
            applied_subscription=self.subscription,
        )
        self.audit.append(("subscription_edited", self.subscription.id))
        return self.draft, LifecycleTransition(
            LifecycleAction.EDITED,
            self.subscription,
        )

    async def pause(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
    ) -> LifecycleTransition:
        if await self.get_owned_including_deleted(user_id, subscription_id) is None:
            return LifecycleTransition(LifecycleAction.NOT_FOUND)
        if self.subscription.status is SubscriptionStatus.PAUSED:
            return LifecycleTransition(
                LifecycleAction.ALREADY_APPLIED,
                self.subscription,
            )
        if self.subscription.status is not SubscriptionStatus.ACTIVE:
            return LifecycleTransition(
                LifecycleAction.INVALID_STATE,
                self.subscription,
            )
        self.subscription = replace(
            self.subscription,
            status=SubscriptionStatus.PAUSED,
            manual_check_in_progress=False,
            updated_at=now,
        )
        self.audit.append(("subscription_paused", subscription_id))
        return LifecycleTransition(LifecycleAction.PAUSED, self.subscription)

    async def resume(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
    ) -> LifecycleTransition:
        if await self.get_owned_including_deleted(user_id, subscription_id) is None:
            return LifecycleTransition(LifecycleAction.NOT_FOUND)
        if self.subscription.status is SubscriptionStatus.ACTIVE:
            return LifecycleTransition(
                LifecycleAction.ALREADY_APPLIED,
                self.subscription,
            )
        if self.subscription.status is not SubscriptionStatus.PAUSED:
            return LifecycleTransition(
                LifecycleAction.INVALID_STATE,
                self.subscription,
            )
        self.subscription = replace(
            self.subscription,
            status=SubscriptionStatus.ACTIVE,
            availability_state=AvailabilityState.UNKNOWN,
            updated_at=now,
        )
        self.audit.append(("subscription_resumed", subscription_id))
        return LifecycleTransition(LifecycleAction.RESUMED, self.subscription)

    async def delete(
        self,
        user_id: int,
        subscription_id: int,
        *,
        expected_version: int,
        now: datetime,
    ) -> LifecycleTransition:
        if await self.get_owned_including_deleted(user_id, subscription_id) is None:
            return LifecycleTransition(LifecycleAction.NOT_FOUND)
        if self.subscription.status is SubscriptionStatus.DELETED:
            return LifecycleTransition(
                LifecycleAction.ALREADY_APPLIED,
                self.subscription,
            )
        current_version = int(
            (self.subscription.updated_at or self.subscription.created_at).timestamp()
        )
        if current_version != expected_version:
            return LifecycleTransition(LifecycleAction.STALE, self.subscription)
        self.subscription = replace(
            self.subscription,
            status=SubscriptionStatus.DELETED,
            updated_at=now,
        )
        self.audit.append(("subscription_deleted", subscription_id))
        return LifecycleTransition(LifecycleAction.DELETED, self.subscription)


class FixedValidator:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid

    async def can_resume(self, subscription: Subscription) -> bool:
        return self.valid
