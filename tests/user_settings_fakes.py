from __future__ import annotations

from dataclasses import replace
from datetime import time

from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    MonitoringFilters,
)
from pharmacy_bot.domain.user_settings import (
    SettingsStatus,
    SupportedLanguage,
    Usage,
    UserPreferences,
)


class InMemorySettingsRepository:
    def __init__(self, *, active_subscriptions: int = 0) -> None:
        self.active_subscriptions = active_subscriptions
        self.value: UserPreferences | None = None

    async def get_or_create(self, user_id: int) -> UserPreferences:
        if self.value is None:
            self.value = UserPreferences(
                user_id=user_id,
                generation=1,
                language=SupportedLanguage.RU,
                timezone_name="Europe/Moscow",
                default_location=None,
                default_radius_meters=None,
                default_source_codes=(),
                filters=MonitoringFilters(),
                completion_mode=CompletionMode.CONTINUE,
                quiet_hours_enabled=False,
                quiet_hours_start=time(22),
                quiet_hours_end=time(8),
                digest_enabled=False,
                max_points_per_message=5,
                status=SettingsStatus.IDLE,
            )
        return self.value

    async def save(
        self,
        preferences: UserPreferences,
        *,
        expected_generation: int,
    ) -> UserPreferences | None:
        if self.value is None or self.value.generation != expected_generation:
            return None
        self.value = replace(preferences, generation=expected_generation + 1)
        return self.value

    async def usage(self, user_id: int, max_active: int) -> Usage:
        return Usage(self.active_subscriptions, max_active)
