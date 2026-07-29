from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from pharmacy_bot.application.subscriptions import (
    CheckEnqueueResult,
    CheckGate,
    CheckGateResult,
    SubscriptionFilter,
    SubscriptionPage,
)
from pharmacy_bot.domain.subscription_setup import (
    AvailabilityState,
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    MonitoringFilters,
    ProductSnapshot,
    Subscription,
    SubscriptionStatus,
)


def subscription(
    identifier: int,
    user_id: int = 1,
    *,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    state: AvailabilityState = AvailabilityState.PENDING,
) -> Subscription:
    return Subscription(
        id=identifier,
        user_id=user_id,
        product=ProductSnapshot(
            f"product-{identifier}",
            "v1",
            f"Товар {identifier}",
            "таблетки",
            "10 мг",
            "№20",
            "Производитель",
            "source.example",
        ),
        location=LocationCandidate(
            "city:moscow",
            LocationInputMode.CITY,
            "Москва",
            city="Москва",
            confidence=LocationConfidence.EXACT,
        ),
        radius_meters=5000,
        source_codes=("source-a",),
        filters=MonitoringFilters(),
        completion_mode=CompletionMode.CONTINUE,
        ends_at=None,
        status=status,
        availability_state=state,
        created_at=datetime(2026, 7, 29, 12, identifier, tzinfo=UTC),
        updated_at=datetime(2026, 7, 29, 12, identifier, tzinfo=UTC),
    )


class InMemorySubscriptionRepository:
    def __init__(self, items: tuple[Subscription, ...]) -> None:
        self.items = {item.id: item for item in items}
        self.gate: CheckGate | None = None
        self.retry_at: datetime | None = None

    async def list_owned(
        self,
        user_id: int,
        selected_filter: SubscriptionFilter,
        *,
        page: int,
        page_size: int,
    ) -> SubscriptionPage:
        values = [
            item
            for item in self.items.values()
            if item.user_id == user_id
            and item.status is not SubscriptionStatus.DELETED
            and (
                selected_filter is SubscriptionFilter.ALL
                or item.status.value == selected_filter.value
            )
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        total_pages = max(1, (len(values) + page_size - 1) // page_size)
        safe_page = max(0, min(page, total_pages - 1))
        return SubscriptionPage(
            tuple(values[safe_page * page_size : (safe_page + 1) * page_size]),
            safe_page,
            total_pages,
            len(values),
            version=42,
        )

    async def get_owned(
        self,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None:
        item = self.items.get(subscription_id)
        return item if item and item.user_id == user_id else None

    async def begin_manual_check(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
        cooldown: timedelta,
    ) -> CheckGateResult:
        item = await self.get_owned(user_id, subscription_id)
        if item is None:
            return CheckGateResult(CheckGate.NOT_FOUND)
        gate = self.gate or CheckGate.ACCEPTED
        if gate is CheckGate.ACCEPTED:
            item = replace(
                item,
                manual_check_in_progress=True,
                next_manual_check_at=now + cooldown,
            )
            self.items[item.id] = item
        return CheckGateResult(gate, item, self.retry_at)

    async def mark_manual_check_failed(
        self,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None:
        item = await self.get_owned(user_id, subscription_id)
        if item:
            item = replace(item, manual_check_in_progress=False)
            self.items[item.id] = item
        return item


class FakeScheduler:
    def __init__(
        self,
        result: CheckEnqueueResult = CheckEnqueueResult.QUEUED,
    ) -> None:
        self.result = result
        self.calls = 0

    async def enqueue(self, value: Subscription) -> CheckEnqueueResult:
        self.calls += 1
        return self.result
