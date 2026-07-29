from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from pharmacy_bot.application.onboarding import OnboardingResult, OnboardingService, OnboardingView
from pharmacy_bot.application.subscription_setup import LocationResolution, LocationResolver
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.subscription_lifecycle import (
    EditStatus,
    LifecycleAction,
    LifecycleTransition,
    SubscriptionEditDraft,
)
from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    ProductSnapshot,
    SourceOption,
    Subscription,
    SubscriptionStatus,
)


class LifecycleView(StrEnum):
    ONBOARDING = "onboarding"
    NOT_FOUND = "not_found"
    CHOOSE_BLOCK = "choose_block"
    AWAITING_LOCATION = "awaiting_location"
    LOCATION_RESULTS = "location_results"
    CHOOSE_RADIUS = "choose_radius"
    CHOOSE_SOURCES = "choose_sources"
    CHOOSE_FILTERS = "choose_filters"
    CHOOSE_COMPLETION = "choose_completion"
    AWAITING_END_DATE = "awaiting_end_date"
    REVIEW = "review"
    APPLIED = "applied"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    RESUMED = "resumed"
    DELETE_CONFIRM = "delete_confirm"
    DELETED = "deleted"
    INVALID_CONFIGURATION = "invalid_configuration"
    INPUT_ERROR = "input_error"
    TEMPORARY_ERROR = "temporary_error"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    view: LifecycleView
    onboarding: OnboardingResult
    subscription: Subscription | None = None
    draft: SubscriptionEditDraft | None = None
    error: str | None = None
    version: int = 0


class LifecycleRepository(Protocol):
    async def get_owned_including_deleted(
        self,
        user_id: int,
        subscription_id: int,
    ) -> Subscription | None: ...

    async def start_edit(
        self,
        user_id: int,
        subscription: Subscription,
        sources: tuple[SourceOption, ...],
        *,
        now: datetime,
        expires_at: datetime,
    ) -> SubscriptionEditDraft: ...

    async def get_edit(self, user_id: int) -> SubscriptionEditDraft | None: ...

    async def save_edit(
        self,
        draft: SubscriptionEditDraft,
        *,
        expected_generation: int,
    ) -> SubscriptionEditDraft | None: ...

    async def apply_edit(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
    ) -> tuple[SubscriptionEditDraft, LifecycleTransition] | None: ...

    async def pause(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
    ) -> LifecycleTransition: ...

    async def resume(
        self,
        user_id: int,
        subscription_id: int,
        *,
        now: datetime,
    ) -> LifecycleTransition: ...

    async def delete(
        self,
        user_id: int,
        subscription_id: int,
        *,
        expected_version: int,
        now: datetime,
    ) -> LifecycleTransition: ...


class SourceCapabilities(Protocol):
    async def available_sources(
        self,
        product: ProductSnapshot,
        location: LocationCandidate,
    ) -> tuple[SourceOption, ...]: ...


class ConfigurationValidator(Protocol):
    async def can_resume(self, subscription: Subscription) -> bool: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SubscriptionLifecycleService:
    def __init__(
        self,
        onboarding: OnboardingService,
        repository: LifecycleRepository,
        locations: LocationResolver,
        sources: SourceCapabilities,
        validator: ConfigurationValidator,
        *,
        draft_ttl: timedelta,
        min_radius_meters: int,
        max_radius_meters: int,
        location_min_length: int,
        location_max_length: int,
        timezone_name: str = "Europe/Moscow",
        clock: Clock | None = None,
    ) -> None:
        self._onboarding = onboarding
        self._repository = repository
        self._locations = locations
        self._sources = sources
        self._validator = validator
        self._draft_ttl = draft_ttl
        self._min_radius = min_radius_meters
        self._max_radius = max_radius_meters
        self._location_min = location_min_length
        self._location_max = location_max_length
        self._timezone = ZoneInfo(timezone_name)
        self._clock = clock or SystemClock()

    async def start_edit(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> LifecycleResult:
        context = await self._subscription_context(identity, subscription_id)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, subscription = context
        if subscription.status is SubscriptionStatus.DELETED:
            return LifecycleResult(LifecycleView.NOT_FOUND, onboarding)
        sources = await self._sources.available_sources(
            subscription.product,
            subscription.location,
        )
        now = self._clock.now()
        draft = await self._repository.start_edit(
            onboarding.user.id,
            subscription,
            sources,
            now=now,
            expires_at=now + self._draft_ttl,
        )
        return self._from_draft(onboarding, draft)

    async def choose_block(
        self,
        identity: TelegramIdentity,
        generation: int,
        block: int,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        statuses = {
            1: EditStatus.AWAITING_LOCATION,
            2: EditStatus.CHOOSE_SOURCES,
            3: EditStatus.CHOOSE_FILTERS,
            4: EditStatus.CHOOSE_COMPLETION,
            5: EditStatus.REVIEW,
        }
        status = statuses.get(block)
        if status is None:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        updated = replace(
            draft,
            status=status,
            location_mode=None if block == 1 else draft.location_mode,
        )
        return await self._save(onboarding, updated, generation)

    async def choose_location_mode(
        self,
        identity: TelegramIdentity,
        generation: int,
        mode: LocationInputMode,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        return await self._save(
            onboarding,
            replace(
                draft,
                status=EditStatus.AWAITING_LOCATION,
                location_mode=mode,
                location_candidates=(),
            ),
            generation,
        )

    async def accepts_text(self, identity: TelegramIdentity) -> bool:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return False
        draft = await self._repository.get_edit(onboarding.user.id)
        return bool(
            draft
            and draft.expires_at > self._clock.now()
            and draft.status in {EditStatus.AWAITING_LOCATION, EditStatus.AWAITING_END_DATE}
        )

    async def submit_text(
        self,
        identity: TelegramIdentity,
        text: str,
    ) -> LifecycleResult | None:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is EditStatus.AWAITING_END_DATE:
            return await self._submit_end_date(onboarding, draft, text)
        if draft.status is not EditStatus.AWAITING_LOCATION or draft.location_mode is None:
            return None
        normalized = self._normalize(text)
        if not self._location_min <= len(normalized) <= self._location_max:
            return LifecycleResult(
                LifecycleView.INPUT_ERROR,
                onboarding,
                draft=draft,
                error=(f"Введите от {self._location_min} до {self._location_max} символов."),
            )
        resolution = await self._locations.resolve(draft.location_mode, normalized)
        return await self._apply_location_resolution(onboarding, draft, resolution)

    async def submit_coordinates(
        self,
        identity: TelegramIdentity,
        latitude: float,
        longitude: float,
    ) -> LifecycleResult | None:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if (
            draft.status is not EditStatus.AWAITING_LOCATION
            or draft.location_mode is not LocationInputMode.COORDINATES
        ):
            return None
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
                draft,
                status=EditStatus.CHOOSE_RADIUS,
                location_candidates=(location,),
                location=location,
            ),
            draft.generation,
        )

    async def select_location(
        self,
        identity: TelegramIdentity,
        generation: int,
        ordinal: int,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is not EditStatus.CONFIRM_LOCATION or draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        location = next(
            (item for item in draft.location_candidates if item.ordinal == ordinal),
            None,
        )
        if location is None:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        return await self._save(
            onboarding,
            replace(draft, status=EditStatus.CHOOSE_RADIUS, location=location),
            generation,
        )

    async def set_radius(
        self,
        identity: TelegramIdentity,
        generation: int,
        radius_meters: int,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is not EditStatus.CHOOSE_RADIUS or draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        if not self._min_radius <= radius_meters <= self._max_radius:
            return LifecycleResult(
                LifecycleView.INPUT_ERROR,
                onboarding,
                draft=draft,
                error="Радиус выходит за системные границы.",
            )
        sources = await self._sources.available_sources(draft.original.product, draft.location)
        sources = tuple(replace(item, ordinal=index) for index, item in enumerate(sources))
        available = {item.code for item in sources if item.available}
        selected = tuple(code for code in draft.selected_source_codes if code in available)
        return await self._save(
            onboarding,
            replace(
                draft,
                status=EditStatus.CHOOSE_BLOCK,
                radius_meters=radius_meters,
                available_sources=sources,
                selected_source_codes=selected,
            ),
            generation,
        )

    async def toggle_source(
        self,
        identity: TelegramIdentity,
        generation: int,
        ordinal: int,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is not EditStatus.CHOOSE_SOURCES or draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        source = next((item for item in draft.available_sources if item.ordinal == ordinal), None)
        if source is None or not source.available:
            return LifecycleResult(
                LifecycleView.INPUT_ERROR,
                onboarding,
                draft=draft,
                error="Источник недоступен.",
            )
        selected = set(draft.selected_source_codes)
        selected.symmetric_difference_update({source.code})
        return await self._save(
            onboarding,
            replace(draft, selected_source_codes=tuple(sorted(selected))),
            generation,
        )

    async def finish_sources(
        self,
        identity: TelegramIdentity,
        generation: int,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is not EditStatus.CHOOSE_SOURCES or draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        active = {item.code for item in draft.available_sources if item.available}
        if not draft.selected_source_codes or not set(draft.selected_source_codes) <= active:
            return LifecycleResult(
                LifecycleView.INPUT_ERROR,
                onboarding,
                draft=draft,
                error="Выберите хотя бы один работающий источник.",
            )
        return await self._save(
            onboarding,
            replace(draft, status=EditStatus.CHOOSE_BLOCK),
            generation,
        )

    async def toggle_filter(
        self,
        identity: TelegramIdentity,
        generation: int,
        code: int,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is not EditStatus.CHOOSE_FILTERS or draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        filters = draft.filters
        if code == 1:
            if not filters.notify_low_stock and not self._supports_filter(
                draft,
                "supports_low_stock",
            ):
                return LifecycleResult(
                    LifecycleView.INPUT_ERROR,
                    onboarding,
                    draft=draft,
                    error="Выбранные источники не передают остатки.",
                )
            filters = replace(filters, notify_low_stock=not filters.notify_low_stock)
        elif code == 2:
            if not filters.notify_orderable and not self._supports_filter(
                draft,
                "supports_orderable",
            ):
                return LifecycleResult(
                    LifecycleView.INPUT_ERROR,
                    onboarding,
                    draft=draft,
                    error="Выбранные источники не передают возможность заказа.",
                )
            filters = replace(filters, notify_orderable=not filters.notify_orderable)
        elif code == 3:
            if not filters.include_price and not self._supports_filter(
                draft,
                "supports_price",
            ):
                return LifecycleResult(
                    LifecycleView.INPUT_ERROR,
                    onboarding,
                    draft=draft,
                    error="Выбранные источники не передают цену.",
                )
            filters = replace(filters, include_price=not filters.include_price)
        else:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        return await self._save(
            onboarding,
            replace(draft, filters=filters),
            generation,
        )

    async def finish_filters(
        self,
        identity: TelegramIdentity,
        generation: int,
    ) -> LifecycleResult:
        return await self._return_to_blocks(
            identity,
            generation,
            EditStatus.CHOOSE_FILTERS,
        )

    async def back_to_blocks(
        self,
        identity: TelegramIdentity,
        generation: int,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        return await self._save(
            onboarding,
            replace(draft, status=EditStatus.CHOOSE_BLOCK, location_mode=None),
            generation,
        )

    async def choose_completion(
        self,
        identity: TelegramIdentity,
        generation: int,
        mode: CompletionMode,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is not EditStatus.CHOOSE_COMPLETION or draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        status = (
            EditStatus.AWAITING_END_DATE
            if mode is CompletionMode.UNTIL_DATE
            else EditStatus.CHOOSE_BLOCK
        )
        return await self._save(
            onboarding,
            replace(draft, status=status, completion_mode=mode, ends_at=None),
            generation,
        )

    async def apply(
        self,
        identity: TelegramIdentity,
        generation: int,
    ) -> LifecycleResult:
        context = await self._edit_context(identity, allow_applied=True)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is EditStatus.APPLIED and draft.applied_subscription:
            return LifecycleResult(
                LifecycleView.APPLIED,
                onboarding,
                subscription=draft.applied_subscription,
                draft=draft,
            )
        if draft.status is not EditStatus.REVIEW or draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        transition = await self._repository.apply_edit(
            onboarding.user.id,
            expected_generation=generation,
            now=self._clock.now(),
        )
        if transition is None:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        saved, outcome = transition
        if outcome.action is LifecycleAction.STALE:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=saved)
        if outcome.action is LifecycleAction.CONFIGURATION_INVALID:
            return LifecycleResult(
                LifecycleView.INVALID_CONFIGURATION,
                onboarding,
                subscription=outcome.subscription,
                draft=saved,
            )
        return LifecycleResult(
            LifecycleView.APPLIED,
            onboarding,
            subscription=outcome.subscription,
            draft=saved,
        )

    async def cancel_edit(self, identity: TelegramIdentity) -> LifecycleResult | None:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return None
        onboarding, draft = context
        saved = await self._repository.save_edit(
            replace(draft, status=EditStatus.CANCELLED),
            expected_generation=draft.generation,
        )
        return (
            LifecycleResult(LifecycleView.CANCELLED, onboarding, draft=saved)
            if saved
            else LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        )

    async def pause(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> LifecycleResult:
        return await self._transition(identity, subscription_id, resume=False)

    async def toggle_pause(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> LifecycleResult:
        context = await self._subscription_context(identity, subscription_id)
        if isinstance(context, LifecycleResult):
            return context
        _, subscription = context
        if subscription.status is SubscriptionStatus.PAUSED:
            return await self.resume(identity, subscription_id)
        return await self.pause(identity, subscription_id)

    async def resume(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> LifecycleResult:
        context = await self._subscription_context(identity, subscription_id)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, subscription = context
        if (
            subscription.status is SubscriptionStatus.PAUSED
            and not await self._validator.can_resume(subscription)
        ):
            return LifecycleResult(
                LifecycleView.INVALID_CONFIGURATION,
                onboarding,
                subscription=subscription,
            )
        transition = await self._repository.resume(
            onboarding.user.id,
            subscription_id,
            now=self._clock.now(),
        )
        return self._transition_result(onboarding, transition, LifecycleView.RESUMED)

    async def request_delete(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> LifecycleResult:
        context = await self._subscription_context(identity, subscription_id)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, subscription = context
        if subscription.status is SubscriptionStatus.DELETED:
            return LifecycleResult(LifecycleView.NOT_FOUND, onboarding)
        return LifecycleResult(
            LifecycleView.DELETE_CONFIRM,
            onboarding,
            subscription=subscription,
            version=self._version(subscription),
        )

    async def confirm_delete(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
        expected_version: int,
    ) -> LifecycleResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return LifecycleResult(LifecycleView.ONBOARDING, onboarding)
        transition = await self._repository.delete(
            onboarding.user.id,
            subscription_id,
            expected_version=expected_version,
            now=self._clock.now(),
        )
        return self._transition_result(onboarding, transition, LifecycleView.DELETED)

    async def _transition(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
        *,
        resume: bool,
    ) -> LifecycleResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return LifecycleResult(LifecycleView.ONBOARDING, onboarding)
        transition = (
            await self._repository.resume(
                onboarding.user.id,
                subscription_id,
                now=self._clock.now(),
            )
            if resume
            else await self._repository.pause(
                onboarding.user.id,
                subscription_id,
                now=self._clock.now(),
            )
        )
        return self._transition_result(onboarding, transition, LifecycleView.PAUSED)

    @staticmethod
    def _transition_result(
        onboarding: OnboardingResult,
        transition: LifecycleTransition,
        success_view: LifecycleView,
    ) -> LifecycleResult:
        if transition.action is LifecycleAction.NOT_FOUND:
            return LifecycleResult(LifecycleView.NOT_FOUND, onboarding)
        if transition.action is LifecycleAction.STALE:
            return LifecycleResult(
                LifecycleView.STALE,
                onboarding,
                subscription=transition.subscription,
            )
        if transition.action in {
            LifecycleAction.INVALID_STATE,
            LifecycleAction.CONFIGURATION_INVALID,
        }:
            return LifecycleResult(
                LifecycleView.INVALID_CONFIGURATION,
                onboarding,
                subscription=transition.subscription,
            )
        return LifecycleResult(
            success_view,
            onboarding,
            subscription=transition.subscription,
        )

    @staticmethod
    def _supports_filter(
        draft: SubscriptionEditDraft,
        attribute: str,
    ) -> bool:
        selected = set(draft.selected_source_codes)
        return any(
            item.code in selected and item.available and bool(getattr(item, attribute))
            for item in draft.available_sources
        )

    async def _apply_location_resolution(
        self,
        onboarding: OnboardingResult,
        draft: SubscriptionEditDraft,
        resolution: LocationResolution,
    ) -> LifecycleResult:
        if resolution.temporary_error:
            return LifecycleResult(
                LifecycleView.TEMPORARY_ERROR,
                onboarding,
                draft=draft,
            )
        if not resolution.candidates:
            return LifecycleResult(
                LifecycleView.INPUT_ERROR,
                onboarding,
                draft=draft,
                error="Локация не найдена.",
            )
        candidates = tuple(
            replace(item, ordinal=index) for index, item in enumerate(resolution.candidates)
        )
        exact = len(candidates) == 1 and candidates[0].confidence is LocationConfidence.EXACT
        return await self._save(
            onboarding,
            replace(
                draft,
                status=EditStatus.CHOOSE_RADIUS if exact else EditStatus.CONFIRM_LOCATION,
                location_candidates=candidates,
                location=candidates[0] if exact else draft.location,
            ),
            draft.generation,
        )

    async def _submit_end_date(
        self,
        onboarding: OnboardingResult,
        draft: SubscriptionEditDraft,
        text: str,
    ) -> LifecycleResult:
        try:
            value = datetime.strptime(self._normalize(text), "%d.%m.%Y").date()
        except ValueError:
            return LifecycleResult(
                LifecycleView.INPUT_ERROR,
                onboarding,
                draft=draft,
                error="Введите дату в формате ДД.ММ.ГГГГ.",
            )
        ends_at = datetime.combine(value, time(23, 59, 59), tzinfo=self._timezone).astimezone(UTC)
        if ends_at <= self._clock.now():
            return LifecycleResult(
                LifecycleView.INPUT_ERROR,
                onboarding,
                draft=draft,
                error="Дата окончания должна быть в будущем.",
            )
        return await self._save(
            onboarding,
            replace(
                draft,
                status=EditStatus.CHOOSE_BLOCK,
                completion_mode=CompletionMode.UNTIL_DATE,
                ends_at=ends_at,
            ),
            draft.generation,
        )

    async def _return_to_blocks(
        self,
        identity: TelegramIdentity,
        generation: int,
        expected: EditStatus,
    ) -> LifecycleResult:
        context = await self._edit_context(identity)
        if isinstance(context, LifecycleResult):
            return context
        onboarding, draft = context
        if draft.status is not expected or draft.generation != generation:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        return await self._save(
            onboarding,
            replace(draft, status=EditStatus.CHOOSE_BLOCK),
            generation,
        )

    async def _subscription_context(
        self,
        identity: TelegramIdentity,
        subscription_id: int,
    ) -> tuple[OnboardingResult, Subscription] | LifecycleResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return LifecycleResult(LifecycleView.ONBOARDING, onboarding)
        subscription = await self._repository.get_owned_including_deleted(
            onboarding.user.id,
            subscription_id,
        )
        if subscription is None:
            return LifecycleResult(LifecycleView.NOT_FOUND, onboarding)
        return onboarding, subscription

    async def _edit_context(
        self,
        identity: TelegramIdentity,
        *,
        allow_applied: bool = False,
    ) -> tuple[OnboardingResult, SubscriptionEditDraft] | LifecycleResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return LifecycleResult(LifecycleView.ONBOARDING, onboarding)
        draft = await self._repository.get_edit(onboarding.user.id)
        if (
            draft is None
            or draft.expires_at <= self._clock.now()
            or (draft.status is EditStatus.APPLIED and not allow_applied)
            or draft.status is EditStatus.CANCELLED
        ):
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        return onboarding, draft

    async def _save(
        self,
        onboarding: OnboardingResult,
        draft: SubscriptionEditDraft,
        expected_generation: int,
    ) -> LifecycleResult:
        saved = await self._repository.save_edit(
            draft,
            expected_generation=expected_generation,
        )
        if saved is None:
            return LifecycleResult(LifecycleView.STALE, onboarding, draft=draft)
        return self._from_draft(onboarding, saved)

    @staticmethod
    def _from_draft(
        onboarding: OnboardingResult,
        draft: SubscriptionEditDraft,
    ) -> LifecycleResult:
        views = {
            EditStatus.CHOOSE_BLOCK: LifecycleView.CHOOSE_BLOCK,
            EditStatus.AWAITING_LOCATION: LifecycleView.AWAITING_LOCATION,
            EditStatus.CONFIRM_LOCATION: LifecycleView.LOCATION_RESULTS,
            EditStatus.CHOOSE_RADIUS: LifecycleView.CHOOSE_RADIUS,
            EditStatus.CHOOSE_SOURCES: LifecycleView.CHOOSE_SOURCES,
            EditStatus.CHOOSE_FILTERS: LifecycleView.CHOOSE_FILTERS,
            EditStatus.CHOOSE_COMPLETION: LifecycleView.CHOOSE_COMPLETION,
            EditStatus.AWAITING_END_DATE: LifecycleView.AWAITING_END_DATE,
            EditStatus.REVIEW: LifecycleView.REVIEW,
            EditStatus.APPLIED: LifecycleView.APPLIED,
            EditStatus.CANCELLED: LifecycleView.CANCELLED,
        }
        return LifecycleResult(
            views[draft.status],
            onboarding,
            subscription=draft.applied_subscription,
            draft=draft,
        )

    @staticmethod
    def _version(subscription: Subscription) -> int:
        value = subscription.updated_at or subscription.created_at
        return int(value.timestamp())

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).split())
