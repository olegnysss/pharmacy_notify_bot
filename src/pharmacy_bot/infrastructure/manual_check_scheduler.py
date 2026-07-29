from pharmacy_bot.application.subscriptions import CheckEnqueueResult
from pharmacy_bot.domain.subscription_setup import Subscription


class UnavailableManualCheckScheduler:
    async def enqueue(self, subscription: Subscription) -> CheckEnqueueResult:
        return CheckEnqueueResult.TEMPORARY_ERROR


class DemoManualCheckScheduler:
    async def enqueue(self, subscription: Subscription) -> CheckEnqueueResult:
        return CheckEnqueueResult.QUEUED
