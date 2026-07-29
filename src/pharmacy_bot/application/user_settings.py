from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pharmacy_bot.application.onboarding import OnboardingResult, OnboardingService, OnboardingView
from pharmacy_bot.application.subscription_setup import LocationResolver
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    ProductSnapshot,
    SourceOption,
)
from pharmacy_bot.domain.user_settings import (
    ServiceLimits,
    SettingsStatus,
    SupportedLanguage,
    Usage,
    UserPreferences,
)


class SettingsView(StrEnum):
    ONBOARDING = "onboarding"
    DASHBOARD = "dashboard"
    CHOOSE_LOCATION = "choose_location"
    AWAITING_LOCATION = "awaiting_location"
    LOCATION_RESULTS = "location_results"
    CHOOSE_RADIUS = "choose_radius"
    CHOOSE_SOURCES = "choose_sources"
    LANGUAGE = "language"
    TIMEZONE = "timezone"
    NOTIFICATIONS = "notifications"
    LIMITS = "limits"
    SAVED = "saved"
    INPUT_ERROR = "input_error"
    TEMPORARY_ERROR = "temporary_error"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class SettingsResult:
    view: SettingsView
    onboarding: OnboardingResult
    preferences: UserPreferences | None = None
    sources: tuple[SourceOption, ...] = ()
    usage: Usage | None = None
    limits: ServiceLimits | None = None
    error: str | None = None


class SettingsRepository(Protocol):
    async def get_or_create(self, user_id: int) -> UserPreferences: ...

    async def save(
        self,
        preferences: UserPreferences,
        *,
        expected_generation: int,
    ) -> UserPreferences | None: ...

    async def usage(self, user_id: int, max_active: int) -> Usage: ...


class SourceCapabilities(Protocol):
    async def available_sources(
        self,
        product: ProductSnapshot,
        location: LocationCandidate,
    ) -> tuple[SourceOption, ...]: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UserSettingsService:
    _TIMEZONES = ("Europe/Moscow", "Europe/Kaliningrad", "Asia/Yekaterinburg", "UTC")
    _SETTINGS_PRODUCT = ProductSnapshot(
        candidate_key="settings-capabilities",
        version="v1",
        name="Настройки по умолчанию",
        form=None,
        dosage=None,
        package=None,
        manufacturer=None,
        source_host=None,
    )

    def __init__(
        self,
        onboarding: OnboardingService,
        repository: SettingsRepository,
        locations: LocationResolver,
        sources: SourceCapabilities,
        limits: ServiceLimits,
        *,
        max_points_per_message: int = 20,
        editor_ttl: timedelta = timedelta(hours=2),
        clock: Clock | None = None,
    ) -> None:
        self._onboarding = onboarding
        self._repository = repository
        self._locations = locations
        self._sources = sources
        self._limits = limits
        self._max_points = max_points_per_message
        self._editor_ttl = editor_ttl
        self._clock = clock or SystemClock()

    async def open(
        self,
        identity: TelegramIdentity,
        *,
        location_only: bool = False,
    ) -> SettingsResult:
        context = await self._context(identity)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        return SettingsResult(
            SettingsView.CHOOSE_LOCATION if location_only else SettingsView.DASHBOARD,
            onboarding,
            preferences,
            usage=await self._repository.usage(
                onboarding.user.id,
                self._limits.max_active_subscriptions,
            ),
            limits=self._limits,
        )

    async def show_section(
        self,
        identity: TelegramIdentity,
        generation: int,
        section: int,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        views = {
            1: SettingsView.CHOOSE_LOCATION,
            2: SettingsView.LANGUAGE,
            3: SettingsView.TIMEZONE,
            4: SettingsView.NOTIFICATIONS,
            5: SettingsView.LIMITS,
            6: SettingsView.DASHBOARD,
        }
        view = views.get(section)
        if view is None:
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        return await self._result(view, onboarding, preferences)

    async def choose_location_mode(
        self,
        identity: TelegramIdentity,
        generation: int,
        mode: LocationInputMode,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        return await self._save(
            onboarding,
            replace(
                preferences,
                status=SettingsStatus.AWAITING_LOCATION,
                location_mode=mode,
                location_candidates=(),
                editor_expires_at=self._clock.now() + self._editor_ttl,
            ),
            SettingsView.AWAITING_LOCATION,
        )

    async def accepts_text(self, identity: TelegramIdentity) -> bool:
        context = await self._context(identity)
        return bool(
            not isinstance(context, SettingsResult)
            and context[1].status is SettingsStatus.AWAITING_LOCATION
            and context[1].editor_expires_at is not None
            and context[1].editor_expires_at > self._clock.now()
            and context[1].location_mode
            in {
                LocationInputMode.CITY,
                LocationInputMode.ADDRESS,
            }
        )

    async def submit_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> SettingsResult | None:
        context = await self._context(identity)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        if (
            preferences.status is not SettingsStatus.AWAITING_LOCATION
            or preferences.editor_expires_at is None
            or preferences.editor_expires_at <= self._clock.now()
            or preferences.location_mode
            not in {
                LocationInputMode.CITY,
                LocationInputMode.ADDRESS,
            }
        ):
            return None
        value = " ".join(unicodedata.normalize("NFKC", text).split())
        if not self._limits.location_min_length <= len(value) <= self._limits.location_max_length:
            return SettingsResult(
                SettingsView.INPUT_ERROR,
                onboarding,
                preferences,
                limits=self._limits,
                error=(
                    f"Введите от {self._limits.location_min_length} до "
                    f"{self._limits.location_max_length} символов."
                ),
            )
        resolution = await self._locations.resolve(preferences.location_mode, value)
        if resolution.temporary_error:
            return SettingsResult(
                SettingsView.TEMPORARY_ERROR,
                onboarding,
                preferences,
                error="Сервис адресов временно недоступен. Введённый текст не потерян.",
            )
        if not resolution.candidates:
            return SettingsResult(
                SettingsView.INPUT_ERROR,
                onboarding,
                preferences,
                error="Локация не найдена. Уточните город или адрес.",
            )
        candidates = tuple(
            replace(candidate, ordinal=index)
            for index, candidate in enumerate(resolution.candidates)
        )
        exact = len(candidates) == 1 and candidates[0].confidence is LocationConfidence.EXACT
        updated = replace(
            preferences,
            status=(SettingsStatus.CHOOSE_RADIUS if exact else SettingsStatus.CONFIRM_LOCATION),
            location_candidates=candidates,
            default_location=candidates[0] if exact else preferences.default_location,
        )
        return await self._save(
            onboarding,
            updated,
            SettingsView.CHOOSE_RADIUS if exact else SettingsView.LOCATION_RESULTS,
        )

    async def submit_coordinates(
        self,
        identity: TelegramIdentity,
        latitude: float,
        longitude: float,
    ) -> SettingsResult | None:
        context = await self._context(identity)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        if (
            preferences.status is not SettingsStatus.AWAITING_LOCATION
            or preferences.location_mode is not LocationInputMode.COORDINATES
        ):
            return None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return SettingsResult(
                SettingsView.INPUT_ERROR,
                onboarding,
                preferences,
                error="Telegram передал некорректные координаты.",
            )
        location = LocationCandidate(
            key=f"telegram:{latitude:.6f}:{longitude:.6f}",
            kind=LocationInputMode.COORDINATES,
            display_name="Переданная геопозиция",
            latitude=latitude,
            longitude=longitude,
            confidence=LocationConfidence.EXACT,
            ordinal=0,
        )
        return await self._save(
            onboarding,
            replace(
                preferences,
                status=SettingsStatus.CHOOSE_RADIUS,
                default_location=location,
                location_candidates=(location,),
            ),
            SettingsView.CHOOSE_RADIUS,
        )

    async def select_location(
        self,
        identity: TelegramIdentity,
        generation: int,
        ordinal: int,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        if preferences.status is not SettingsStatus.CONFIRM_LOCATION:
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        location = next(
            (item for item in preferences.location_candidates if item.ordinal == ordinal),
            None,
        )
        if location is None:
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        return await self._save(
            onboarding,
            replace(
                preferences,
                status=SettingsStatus.CHOOSE_RADIUS,
                default_location=location,
            ),
            SettingsView.CHOOSE_RADIUS,
        )

    async def set_radius(
        self,
        identity: TelegramIdentity,
        generation: int,
        radius_meters: int,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        if (
            preferences.status is not SettingsStatus.CHOOSE_RADIUS
            or preferences.default_location is None
        ):
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        if not self._limits.min_radius_meters <= radius_meters <= self._limits.max_radius_meters:
            return SettingsResult(
                SettingsView.INPUT_ERROR,
                onboarding,
                preferences,
                error="Радиус выходит за централизованные системные ограничения.",
            )
        updated = replace(
            preferences,
            status=SettingsStatus.CHOOSE_SOURCES,
            default_radius_meters=radius_meters,
        )
        return await self._save(
            onboarding,
            updated,
            SettingsView.CHOOSE_SOURCES,
            with_sources=True,
        )

    async def toggle_source(
        self,
        identity: TelegramIdentity,
        generation: int,
        ordinal: int,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        if (
            preferences.status is not SettingsStatus.CHOOSE_SOURCES
            or preferences.default_location is None
        ):
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        sources = await self._available_sources(preferences)
        source = next((item for item in sources if item.ordinal == ordinal), None)
        if source is None or not source.available:
            return SettingsResult(
                SettingsView.INPUT_ERROR,
                onboarding,
                preferences,
                sources,
                error="Этот источник сейчас нельзя сохранить.",
            )
        selected = set(preferences.default_source_codes)
        if source.code in selected:
            selected.remove(source.code)
        elif len(selected) >= self._limits.max_sources_per_subscription:
            return SettingsResult(
                SettingsView.INPUT_ERROR,
                onboarding,
                preferences,
                sources,
                error=(
                    f"Можно выбрать не более "
                    f"{self._limits.max_sources_per_subscription} источников."
                ),
            )
        else:
            selected.add(source.code)
        return await self._save(
            onboarding,
            replace(preferences, default_source_codes=tuple(sorted(selected))),
            SettingsView.CHOOSE_SOURCES,
            with_sources=True,
        )

    async def finish_sources(
        self,
        identity: TelegramIdentity,
        generation: int,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        sources = await self._available_sources(preferences)
        active = {item.code for item in sources if item.available}
        if (
            not preferences.default_source_codes
            or not set(preferences.default_source_codes) <= active
        ):
            return SettingsResult(
                SettingsView.INPUT_ERROR,
                onboarding,
                preferences,
                sources,
                error="Выберите хотя бы один работающий источник.",
            )
        return await self._save(
            onboarding,
            replace(
                preferences,
                status=SettingsStatus.IDLE,
                location_mode=None,
                location_candidates=(),
                editor_expires_at=None,
            ),
            SettingsView.SAVED,
        )

    async def clear_defaults(
        self,
        identity: TelegramIdentity,
        generation: int,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        return await self._save(
            onboarding,
            replace(
                preferences,
                status=SettingsStatus.IDLE,
                default_location=None,
                default_radius_meters=None,
                default_source_codes=(),
                location_mode=None,
                location_candidates=(),
                editor_expires_at=None,
            ),
            SettingsView.SAVED,
        )

    async def set_language(
        self,
        identity: TelegramIdentity,
        generation: int,
        language: SupportedLanguage,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        return await self._save(
            onboarding,
            replace(preferences, language=language, status=SettingsStatus.IDLE),
            SettingsView.SAVED,
        )

    async def set_timezone(
        self,
        identity: TelegramIdentity,
        generation: int,
        ordinal: int,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        if not 0 <= ordinal < len(self._TIMEZONES):
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        timezone_name = self._TIMEZONES[ordinal]
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            timezone_name = "Europe/Moscow"
        return await self._save(
            onboarding,
            replace(
                preferences,
                timezone_name=timezone_name,
                status=SettingsStatus.IDLE,
            ),
            SettingsView.SAVED,
        )

    async def update_notifications(
        self,
        identity: TelegramIdentity,
        generation: int,
        option: int,
    ) -> SettingsResult:
        context = await self._current(identity, generation)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        sources = await self._available_sources(preferences)
        selected = set(preferences.default_source_codes)
        usable = [
            item for item in sources if item.available and (not selected or item.code in selected)
        ]
        filters = preferences.filters
        if option == 1:
            if not filters.notify_low_stock and not any(item.supports_low_stock for item in usable):
                return self._unsupported(onboarding, preferences, sources)
            filters = replace(filters, notify_low_stock=not filters.notify_low_stock)
            preferences = replace(preferences, filters=filters)
        elif option == 2:
            if not filters.notify_orderable and not any(item.supports_orderable for item in usable):
                return self._unsupported(onboarding, preferences, sources)
            filters = replace(filters, notify_orderable=not filters.notify_orderable)
            preferences = replace(preferences, filters=filters)
        elif option == 3:
            if not filters.include_price and not any(item.supports_price for item in usable):
                return self._unsupported(onboarding, preferences, sources)
            filters = replace(filters, include_price=not filters.include_price)
            preferences = replace(preferences, filters=filters)
        elif option == 4:
            preferences = replace(
                preferences,
                quiet_hours_enabled=not preferences.quiet_hours_enabled,
            )
        elif option == 5:
            preferences = replace(preferences, digest_enabled=not preferences.digest_enabled)
        elif option in {6, 7, 8}:
            values = {6: 3, 7: 5, 8: 10}
            points = values[option]
            if points > self._max_points:
                return SettingsResult(
                    SettingsView.INPUT_ERROR,
                    onboarding,
                    preferences,
                    sources,
                    error=f"Допустимо не более {self._max_points} точек в сообщении.",
                )
            preferences = replace(preferences, max_points_per_message=points)
        elif option in {9, 10, 11}:
            modes = {
                9: CompletionMode.CONTINUE,
                10: CompletionMode.PAUSE_AFTER_SUCCESS,
                11: CompletionMode.COMPLETE_AFTER_SUCCESS,
            }
            preferences = replace(preferences, completion_mode=modes[option])
        else:
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        return await self._save(
            onboarding,
            preferences,
            SettingsView.NOTIFICATIONS,
            with_sources=True,
        )

    async def _context(
        self,
        identity: TelegramIdentity,
    ) -> tuple[OnboardingResult, UserPreferences] | SettingsResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return SettingsResult(SettingsView.ONBOARDING, onboarding)
        preferences = await self._repository.get_or_create(onboarding.user.id)
        if preferences.status is not SettingsStatus.IDLE and (
            preferences.editor_expires_at is None
            or preferences.editor_expires_at <= self._clock.now()
        ):
            saved = await self._repository.save(
                replace(
                    preferences,
                    status=SettingsStatus.IDLE,
                    location_mode=None,
                    location_candidates=(),
                    editor_expires_at=None,
                ),
                expected_generation=preferences.generation,
            )
            preferences = saved or preferences
        return onboarding, preferences

    async def _current(
        self,
        identity: TelegramIdentity,
        generation: int,
    ) -> tuple[OnboardingResult, UserPreferences] | SettingsResult:
        context = await self._context(identity)
        if isinstance(context, SettingsResult):
            return context
        onboarding, preferences = context
        if preferences.generation != generation:
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        return context

    async def _save(
        self,
        onboarding: OnboardingResult,
        preferences: UserPreferences,
        view: SettingsView,
        *,
        with_sources: bool = False,
    ) -> SettingsResult:
        saved = await self._repository.save(
            preferences,
            expected_generation=preferences.generation,
        )
        if saved is None:
            return SettingsResult(SettingsView.STALE, onboarding, preferences)
        return await self._result(view, onboarding, saved, with_sources=with_sources)

    async def _result(
        self,
        view: SettingsView,
        onboarding: OnboardingResult,
        preferences: UserPreferences,
        *,
        with_sources: bool = False,
    ) -> SettingsResult:
        return SettingsResult(
            view,
            onboarding,
            preferences,
            await self._available_sources(preferences) if with_sources else (),
            await self._repository.usage(
                onboarding.user.id,
                self._limits.max_active_subscriptions,
            ),
            self._limits,
        )

    async def _available_sources(
        self,
        preferences: UserPreferences,
    ) -> tuple[SourceOption, ...]:
        location = preferences.default_location or LocationCandidate(
            key="settings:anywhere",
            kind=LocationInputMode.CITY,
            display_name="Любая сохранённая локация",
            confidence=LocationConfidence.EXACT,
        )
        values = await self._sources.available_sources(self._SETTINGS_PRODUCT, location)
        return tuple(replace(item, ordinal=index) for index, item in enumerate(values))

    @staticmethod
    def _unsupported(
        onboarding: OnboardingResult,
        preferences: UserPreferences,
        sources: tuple[SourceOption, ...],
    ) -> SettingsResult:
        return SettingsResult(
            SettingsView.INPUT_ERROR,
            onboarding,
            preferences,
            sources,
            error=(
                "Ни один применимый источник не поддерживает эту настройку; она не была включена."
            ),
        )
