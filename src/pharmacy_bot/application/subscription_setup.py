from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from pharmacy_bot.application.onboarding import (
    OnboardingResult,
    OnboardingService,
    OnboardingView,
)
from pharmacy_bot.domain.onboarding import TelegramIdentity
from pharmacy_bot.domain.product_selection import ProductDraft, ProductDraftStatus
from pharmacy_bot.domain.subscription_setup import (
    CompletionMode,
    LocationCandidate,
    LocationConfidence,
    LocationInputMode,
    ProductSnapshot,
    SetupStatus,
    SourceOption,
    Subscription,
    SubscriptionSetupDraft,
)


class SetupView(StrEnum):
    ONBOARDING = "onboarding"
    PRODUCT_REQUIRED = "product_required"
    CHOOSE_LOCATION = "choose_location"
    AWAITING_LOCATION = "awaiting_location"
    LOCATION_RESULTS = "location_results"
    CHOOSE_RADIUS = "choose_radius"
    CHOOSE_SOURCES = "choose_sources"
    CHOOSE_FILTERS = "choose_filters"
    CHOOSE_COMPLETION = "choose_completion"
    AWAITING_END_DATE = "awaiting_end_date"
    REVIEW = "review"
    CREATED = "created"
    CANCELLED = "cancelled"
    INPUT_ERROR = "input_error"
    TEMPORARY_ERROR = "temporary_error"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class SetupResult:
    view: SetupView
    onboarding: OnboardingResult
    draft: SubscriptionSetupDraft | None = None
    subscription: Subscription | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class LocationResolution:
    candidates: tuple[LocationCandidate, ...] = ()
    temporary_error: bool = False


class ProductDraftReader(Protocol):
    async def get(self, user_id: int) -> ProductDraft | None: ...


class SetupRepository(Protocol):
    async def start_or_resume(
        self,
        user_id: int,
        product: ProductSnapshot,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> SubscriptionSetupDraft: ...

    async def get(self, user_id: int) -> SubscriptionSetupDraft | None: ...

    async def save(
        self,
        draft: SubscriptionSetupDraft,
        *,
        expected_generation: int,
    ) -> SubscriptionSetupDraft | None: ...

    async def create_subscription(
        self,
        user_id: int,
        *,
        expected_generation: int,
        now: datetime,
    ) -> tuple[SubscriptionSetupDraft, Subscription] | None: ...


class LocationResolver(Protocol):
    async def resolve(
        self,
        mode: LocationInputMode,
        text: str,
    ) -> LocationResolution: ...


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


class SubscriptionSetupService:
    def __init__(
        self,
        onboarding: OnboardingService,
        product_drafts: ProductDraftReader,
        repository: SetupRepository,
        locations: LocationResolver,
        sources: SourceCapabilities,
        *,
        draft_ttl: timedelta,
        location_min_length: int,
        location_max_length: int,
        min_radius_meters: int,
        max_radius_meters: int,
        timezone_name: str = "Europe/Moscow",
        clock: Clock | None = None,
    ) -> None:
        self._onboarding = onboarding
        self._product_drafts = product_drafts
        self._repository = repository
        self._locations = locations
        self._sources = sources
        self._draft_ttl = draft_ttl
        self._location_min_length = location_min_length
        self._location_max_length = location_max_length
        self._min_radius = min_radius_meters
        self._max_radius = max_radius_meters
        self._timezone = ZoneInfo(timezone_name)
        self._clock = clock or SystemClock()

    async def start(self, identity: TelegramIdentity) -> SetupResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return SetupResult(SetupView.ONBOARDING, onboarding)
        product_draft = await self._product_drafts.get(onboarding.user.id)
        if (
            product_draft is None
            or product_draft.status is not ProductDraftStatus.CONFIRMED
            or product_draft.selected_candidate is None
        ):
            return SetupResult(SetupView.PRODUCT_REQUIRED, onboarding)
        candidate = product_draft.selected_candidate
        product = ProductSnapshot(
            candidate_key=candidate.candidate_key,
            version=candidate.version,
            name=candidate.name,
            form=candidate.form,
            dosage=candidate.dosage,
            package=candidate.package,
            manufacturer=candidate.manufacturer,
            source_host=candidate.source_host,
        )
        now = self._clock.now()
        draft = await self._repository.start_or_resume(
            onboarding.user.id,
            product,
            now=now,
            expires_at=now + self._draft_ttl,
        )
        return self._from_draft(onboarding, draft)

    async def accepts_text(self, identity: TelegramIdentity) -> bool:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return False
        draft = await self._repository.get(onboarding.user.id)
        return bool(
            draft
            and draft.expires_at > self._clock.now()
            and draft.status in {SetupStatus.AWAITING_LOCATION, SetupStatus.AWAITING_END_DATE}
        )

    async def choose_location_mode(
        self,
        identity: TelegramIdentity,
        mode: LocationInputMode,
        generation: int,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        updated = replace(
            draft,
            status=SetupStatus.AWAITING_LOCATION,
            location_mode=mode,
            location_candidates=(),
            location=None,
            radius_meters=None,
            available_sources=(),
            selected_source_codes=(),
        )
        return await self._save(onboarding, updated, generation)

    async def submit_text(self, identity: TelegramIdentity, text: str) -> SetupResult | None:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if draft.status is SetupStatus.AWAITING_END_DATE:
            return await self._submit_end_date(onboarding, draft, text)
        if draft.status is not SetupStatus.AWAITING_LOCATION or draft.location_mode is None:
            return None
        normalized = self._normalize(text)
        if not (self._location_min_length <= len(normalized) <= self._location_max_length):
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error=(
                    f"Введите от {self._location_min_length} до "
                    f"{self._location_max_length} символов."
                ),
            )
        resolution = await self._locations.resolve(draft.location_mode, normalized)
        if resolution.temporary_error:
            return SetupResult(SetupView.TEMPORARY_ERROR, onboarding, draft)
        if not resolution.candidates:
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error="Локация не найдена. Уточните город или адрес.",
            )
        candidates = tuple(
            replace(item, ordinal=index) for index, item in enumerate(resolution.candidates)
        )
        exact = len(candidates) == 1 and candidates[0].confidence is LocationConfidence.EXACT
        updated = replace(
            draft,
            status=SetupStatus.CHOOSE_RADIUS if exact else SetupStatus.CONFIRM_LOCATION,
            location_candidates=candidates,
            location=candidates[0] if exact else None,
        )
        return await self._save(onboarding, updated, draft.generation)

    async def submit_coordinates(
        self,
        identity: TelegramIdentity,
        latitude: float,
        longitude: float,
    ) -> SetupResult | None:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if (
            draft.status is not SetupStatus.AWAITING_LOCATION
            or draft.location_mode is not LocationInputMode.COORDINATES
        ):
            return None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
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
        updated = replace(
            draft,
            status=SetupStatus.CHOOSE_RADIUS,
            location_candidates=(location,),
            location=location,
        )
        return await self._save(onboarding, updated, draft.generation)

    async def select_location(
        self,
        identity: TelegramIdentity,
        generation: int,
        ordinal: int,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if draft.status is not SetupStatus.CONFIRM_LOCATION or draft.generation != generation:
            return SetupResult(SetupView.STALE, onboarding, draft)
        location = next(
            (item for item in draft.location_candidates if item.ordinal == ordinal),
            None,
        )
        if location is None:
            return SetupResult(SetupView.STALE, onboarding, draft)
        return await self._save(
            onboarding,
            replace(draft, status=SetupStatus.CHOOSE_RADIUS, location=location),
            generation,
        )

    async def set_radius(
        self,
        identity: TelegramIdentity,
        generation: int,
        radius_meters: int,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if (
            draft.status is not SetupStatus.CHOOSE_RADIUS
            or draft.location is None
            or draft.generation != generation
        ):
            return SetupResult(SetupView.STALE, onboarding, draft)
        if not self._min_radius <= radius_meters <= self._max_radius:
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error=(
                    f"Радиус должен быть от {self._min_radius // 1000} "
                    f"до {self._max_radius // 1000} км."
                ),
            )
        sources = await self._sources.available_sources(draft.product, draft.location)
        sources = tuple(replace(item, ordinal=index) for index, item in enumerate(sources))
        available_codes = tuple(item.code for item in sources if item.available)
        updated = replace(
            draft,
            status=SetupStatus.CHOOSE_SOURCES,
            radius_meters=radius_meters,
            available_sources=sources,
            selected_source_codes=available_codes,
        )
        return await self._save(onboarding, updated, generation)

    async def toggle_source(
        self,
        identity: TelegramIdentity,
        generation: int,
        ordinal: int,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if draft.status is not SetupStatus.CHOOSE_SOURCES or draft.generation != generation:
            return SetupResult(SetupView.STALE, onboarding, draft)
        source = next((item for item in draft.available_sources if item.ordinal == ordinal), None)
        if source is None or not source.available:
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error=source.unavailable_reason if source else "Источник недоступен.",
            )
        selected = set(draft.selected_source_codes)
        if source.code in selected:
            selected.remove(source.code)
        else:
            selected.add(source.code)
        updated = replace(draft, selected_source_codes=tuple(sorted(selected)))
        return await self._save(onboarding, updated, generation)

    async def confirm_sources(self, identity: TelegramIdentity, generation: int) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if draft.status is not SetupStatus.CHOOSE_SOURCES or draft.generation != generation:
            return SetupResult(SetupView.STALE, onboarding, draft)
        available = {item.code for item in draft.available_sources if item.available}
        if not set(draft.selected_source_codes) & available:
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error="Выберите хотя бы один работающий источник.",
            )
        return await self._save(
            onboarding,
            replace(draft, status=SetupStatus.CHOOSE_FILTERS),
            generation,
        )

    async def toggle_filter(
        self,
        identity: TelegramIdentity,
        generation: int,
        filter_code: int,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if draft.status is not SetupStatus.CHOOSE_FILTERS or draft.generation != generation:
            return SetupResult(SetupView.STALE, onboarding, draft)
        selected_sources = [
            item for item in draft.available_sources if item.code in draft.selected_source_codes
        ]
        filters = draft.filters
        if filter_code == 1 and any(item.supports_low_stock for item in selected_sources):
            filters = replace(filters, notify_low_stock=not filters.notify_low_stock)
        elif filter_code == 2 and any(item.supports_orderable for item in selected_sources):
            filters = replace(filters, notify_orderable=not filters.notify_orderable)
        elif filter_code == 3 and any(item.supports_price for item in selected_sources):
            filters = replace(filters, include_price=not filters.include_price)
        else:
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error="Выбранный фильтр не поддерживается текущими источниками.",
            )
        return await self._save(onboarding, replace(draft, filters=filters), generation)

    async def confirm_filters(self, identity: TelegramIdentity, generation: int) -> SetupResult:
        return await self._advance(
            identity,
            generation,
            SetupStatus.CHOOSE_FILTERS,
            SetupStatus.CHOOSE_COMPLETION,
        )

    async def choose_completion(
        self,
        identity: TelegramIdentity,
        generation: int,
        mode: CompletionMode,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if draft.status is not SetupStatus.CHOOSE_COMPLETION or draft.generation != generation:
            return SetupResult(SetupView.STALE, onboarding, draft)
        next_status = (
            SetupStatus.AWAITING_END_DATE
            if mode is CompletionMode.UNTIL_DATE
            else SetupStatus.REVIEW
        )
        return await self._save(
            onboarding,
            replace(draft, status=next_status, completion_mode=mode, ends_at=None),
            generation,
        )

    async def edit(
        self,
        identity: TelegramIdentity,
        generation: int,
        block: int,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if (
            draft.status in {SetupStatus.CREATED, SetupStatus.CANCELLED}
            or draft.generation != generation
        ):
            return SetupResult(SetupView.STALE, onboarding, draft)
        statuses = {
            1: SetupStatus.CHOOSE_LOCATION,
            2: SetupStatus.CHOOSE_SOURCES,
            3: SetupStatus.CHOOSE_FILTERS,
            4: SetupStatus.CHOOSE_COMPLETION,
        }
        status = statuses.get(block)
        if status is None:
            return SetupResult(SetupView.STALE, onboarding, draft)
        if block >= 2 and (draft.location is None or draft.radius_meters is None):
            return SetupResult(SetupView.STALE, onboarding, draft)
        if block >= 3 and not draft.selected_source_codes:
            return SetupResult(SetupView.STALE, onboarding, draft)
        updated = replace(draft, status=status)
        if block == 1:
            updated = replace(
                updated,
                location_mode=None,
                location_candidates=(),
                location=None,
                radius_meters=None,
                available_sources=(),
                selected_source_codes=(),
            )
        return await self._save(onboarding, updated, generation)

    async def cancel_if_active(self, identity: TelegramIdentity) -> SetupResult | None:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return None
        draft = await self._repository.get(onboarding.user.id)
        if (
            draft is None
            or draft.status in {SetupStatus.CREATED, SetupStatus.CANCELLED}
            or draft.expires_at <= self._clock.now()
        ):
            return None
        saved = await self._repository.save(
            replace(draft, status=SetupStatus.CANCELLED),
            expected_generation=draft.generation,
        )
        return (
            SetupResult(SetupView.CANCELLED, onboarding, saved)
            if saved
            else SetupResult(SetupView.STALE, onboarding, draft)
        )

    async def confirm(self, identity: TelegramIdentity, generation: int) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if draft.status is SetupStatus.CREATED and draft.subscription_id is not None:
            created = await self._repository.create_subscription(
                onboarding.user.id,
                expected_generation=draft.generation,
                now=self._clock.now(),
            )
            if created is None:
                return SetupResult(SetupView.STALE, onboarding, draft)
            return SetupResult(SetupView.CREATED, onboarding, *created)
        if draft.status is not SetupStatus.REVIEW or draft.generation != generation:
            return SetupResult(SetupView.STALE, onboarding, draft)
        if not self._is_complete(draft):
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error="Черновик неполон или выбранные источники больше недоступны.",
            )
        created = await self._repository.create_subscription(
            onboarding.user.id,
            expected_generation=generation,
            now=self._clock.now(),
        )
        if created is None:
            return SetupResult(SetupView.STALE, onboarding, draft)
        return SetupResult(SetupView.CREATED, onboarding, *created)

    async def cancel(
        self,
        identity: TelegramIdentity,
        generation: int | None = None,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if generation is not None and draft.generation != generation:
            return SetupResult(SetupView.STALE, onboarding, draft)
        saved = await self._repository.save(
            replace(draft, status=SetupStatus.CANCELLED),
            expected_generation=draft.generation,
        )
        return (
            SetupResult(SetupView.CANCELLED, onboarding, saved)
            if saved
            else SetupResult(SetupView.STALE, onboarding, draft)
        )

    async def _submit_end_date(
        self,
        onboarding: OnboardingResult,
        draft: SubscriptionSetupDraft,
        text: str,
    ) -> SetupResult:
        normalized = self._normalize(text)
        try:
            local_date = datetime.strptime(normalized, "%d.%m.%Y").date()
        except ValueError:
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error="Введите дату в формате ДД.ММ.ГГГГ.",
            )
        end_local = datetime.combine(local_date, time(23, 59, 59), tzinfo=self._timezone)
        ends_at = end_local.astimezone(UTC)
        if ends_at <= self._clock.now():
            return SetupResult(
                SetupView.INPUT_ERROR,
                onboarding,
                draft,
                error="Дата окончания должна быть в будущем.",
            )
        return await self._save(
            onboarding,
            replace(draft, status=SetupStatus.REVIEW, ends_at=ends_at),
            draft.generation,
        )

    async def _advance(
        self,
        identity: TelegramIdentity,
        generation: int,
        current: SetupStatus,
        target: SetupStatus,
    ) -> SetupResult:
        context = await self._context(identity)
        if isinstance(context, SetupResult):
            return context
        onboarding, draft = context
        if draft.status is not current or draft.generation != generation:
            return SetupResult(SetupView.STALE, onboarding, draft)
        return await self._save(onboarding, replace(draft, status=target), generation)

    async def _context(
        self,
        identity: TelegramIdentity,
    ) -> tuple[OnboardingResult, SubscriptionSetupDraft] | SetupResult:
        onboarding = await self._onboarding.start(identity)
        if onboarding.view is not OnboardingView.MAIN_MENU:
            return SetupResult(SetupView.ONBOARDING, onboarding)
        draft = await self._repository.get(onboarding.user.id)
        if draft is None or draft.expires_at <= self._clock.now():
            return SetupResult(SetupView.STALE, onboarding, draft)
        return onboarding, draft

    async def _save(
        self,
        onboarding: OnboardingResult,
        draft: SubscriptionSetupDraft,
        expected_generation: int,
    ) -> SetupResult:
        saved = await self._repository.save(draft, expected_generation=expected_generation)
        if saved is None:
            return SetupResult(SetupView.STALE, onboarding, draft)
        return self._from_draft(onboarding, saved)

    @staticmethod
    def _is_complete(draft: SubscriptionSetupDraft) -> bool:
        active = {item.code for item in draft.available_sources if item.available}
        return bool(
            draft.location
            and draft.radius_meters
            and draft.completion_mode
            and draft.selected_source_codes
            and set(draft.selected_source_codes) <= active
            and (draft.completion_mode is not CompletionMode.UNTIL_DATE or draft.ends_at)
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).split())

    @staticmethod
    def _from_draft(
        onboarding: OnboardingResult,
        draft: SubscriptionSetupDraft,
    ) -> SetupResult:
        views = {
            SetupStatus.CHOOSE_LOCATION: SetupView.CHOOSE_LOCATION,
            SetupStatus.AWAITING_LOCATION: SetupView.AWAITING_LOCATION,
            SetupStatus.CONFIRM_LOCATION: SetupView.LOCATION_RESULTS,
            SetupStatus.CHOOSE_RADIUS: SetupView.CHOOSE_RADIUS,
            SetupStatus.CHOOSE_SOURCES: SetupView.CHOOSE_SOURCES,
            SetupStatus.CHOOSE_FILTERS: SetupView.CHOOSE_FILTERS,
            SetupStatus.CHOOSE_COMPLETION: SetupView.CHOOSE_COMPLETION,
            SetupStatus.AWAITING_END_DATE: SetupView.AWAITING_END_DATE,
            SetupStatus.REVIEW: SetupView.REVIEW,
            SetupStatus.CREATED: SetupView.CREATED,
            SetupStatus.CANCELLED: SetupView.CANCELLED,
        }
        return SetupResult(views[draft.status], onboarding, draft)
