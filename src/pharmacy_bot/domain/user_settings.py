from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    LocationCandidate,
    LocationInputMode,
    MonitoringFilters,
)


class SupportedLanguage(StrEnum):
    RU = "ru"


class SettingsStatus(StrEnum):
    IDLE = "idle"
    AWAITING_LOCATION = "awaiting_location"
    CONFIRM_LOCATION = "confirm_location"
    CHOOSE_RADIUS = "choose_radius"
    CHOOSE_SOURCES = "choose_sources"


@dataclass(frozen=True, slots=True)
class ServiceLimits:
    min_radius_meters: int
    max_radius_meters: int
    max_sources_per_subscription: int
    max_active_subscriptions: int
    manual_check_cooldown_seconds: int
    location_min_length: int
    location_max_length: int
    product_query_min_length: int
    product_query_max_length: int


@dataclass(frozen=True, slots=True)
class UserPreferences:
    user_id: int
    generation: int
    language: SupportedLanguage
    timezone_name: str
    default_location: LocationCandidate | None
    default_radius_meters: int | None
    default_source_codes: tuple[str, ...]
    filters: MonitoringFilters
    completion_mode: CompletionMode
    quiet_hours_enabled: bool
    quiet_hours_start: time
    quiet_hours_end: time
    digest_enabled: bool
    max_points_per_message: int
    status: SettingsStatus
    location_mode: LocationInputMode | None = None
    location_candidates: tuple[LocationCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class Usage:
    active_subscriptions: int
    max_active_subscriptions: int
