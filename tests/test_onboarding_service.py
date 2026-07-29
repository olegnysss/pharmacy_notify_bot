from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from pharmacy_bot.application.onboarding import (
    DocumentBundle,
    OnboardingService,
    OnboardingView,
)
from pharmacy_bot.domain.onboarding import (
    ConsentDecision,
    ConsentMethod,
    OnboardingStatus,
    TelegramIdentity,
)
from tests.fakes import InMemoryOnboardingRepository


@dataclass(frozen=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@pytest.fixture
def identity() -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=1001,
        telegram_chat_id=1001,
        language_code="ru",
    )


@pytest.fixture
def documents() -> DocumentBundle:
    return DocumentBundle(
        terms_version="terms-v1",
        terms_url="https://example.com/terms",
        privacy_version="privacy-v1",
        privacy_url="https://example.com/privacy",
    )


@pytest.fixture
def accepted_at() -> datetime:
    return datetime(2026, 7, 29, 9, 30, tzinfo=UTC)


@pytest.fixture
def repository() -> InMemoryOnboardingRepository:
    return InMemoryOnboardingRepository()


@pytest.fixture
def service(
    repository: InMemoryOnboardingRepository,
    documents: DocumentBundle,
    accepted_at: datetime,
) -> OnboardingService:
    return OnboardingService(repository, documents, FixedClock(accepted_at))


async def test_first_start_shows_welcome_and_creates_one_pending_user(
    service: OnboardingService,
    repository: InMemoryOnboardingRepository,
    identity: TelegramIdentity,
) -> None:
    first = await service.start(identity)
    second = await service.start(identity)

    assert first.view is OnboardingView.WELCOME
    assert first.user.status is OnboardingStatus.AWAITING_CONSENT
    assert second.view is OnboardingView.CONSENT_REQUIRED
    assert len(repository.users) == 1


async def test_continue_shows_current_document_bundle(
    service: OnboardingService,
    identity: TelegramIdentity,
    documents: DocumentBundle,
) -> None:
    result = await service.continue_onboarding(identity)

    assert result.view is OnboardingView.CONSENT_REQUIRED
    assert result.documents == documents
    assert result.user.status is OnboardingStatus.AWAITING_CONSENT


async def test_accept_persists_version_time_method_and_is_idempotent(
    service: OnboardingService,
    repository: InMemoryOnboardingRepository,
    identity: TelegramIdentity,
    accepted_at: datetime,
) -> None:
    first = await service.accept(identity)
    second = await service.accept(identity)

    assert first.view is OnboardingView.COMPLETED
    assert second.view is OnboardingView.COMPLETED
    assert len(repository.decision_details) == 1
    _, terms, privacy, decision, occurred_at, method = repository.decision_details[0]
    assert (terms, privacy) == ("terms-v1", "privacy-v1")
    assert decision is ConsentDecision.ACCEPTED
    assert occurred_at == accepted_at
    assert method is ConsentMethod.TELEGRAM_INLINE_BUTTON


async def test_decline_is_audited_and_keeps_monitoring_restricted(
    service: OnboardingService,
    repository: InMemoryOnboardingRepository,
    identity: TelegramIdentity,
) -> None:
    first = await service.decline(identity)
    repeated_start = await service.start(identity)

    assert first.view is OnboardingView.DECLINED
    assert repeated_start.view is OnboardingView.DECLINED
    assert repository.decision_details[0][3] is ConsentDecision.DECLINED
    assert len(repository.decision_details) == 1


async def test_user_can_reconsider_decline_and_accept(
    service: OnboardingService,
    repository: InMemoryOnboardingRepository,
    identity: TelegramIdentity,
) -> None:
    await service.decline(identity)

    prompt = await service.continue_onboarding(identity)
    completed = await service.accept(identity)

    assert prompt.view is OnboardingView.CONSENT_REQUIRED
    assert completed.view is OnboardingView.COMPLETED
    assert {item[3] for item in repository.decision_details} == {
        ConsentDecision.DECLINED,
        ConsentDecision.ACCEPTED,
    }


async def test_current_consent_routes_start_to_main_menu(
    service: OnboardingService,
    identity: TelegramIdentity,
) -> None:
    await service.accept(identity)

    result = await service.start(identity)

    assert result.view is OnboardingView.MAIN_MENU
    assert result.user.status is OnboardingStatus.COMPLETED


async def test_new_required_versions_route_completed_user_back_to_consent(
    service: OnboardingService,
    repository: InMemoryOnboardingRepository,
    identity: TelegramIdentity,
) -> None:
    await service.accept(identity)
    changed_documents = DocumentBundle(
        terms_version="terms-v2",
        terms_url="https://example.com/terms-v2",
        privacy_version="privacy-v2",
        privacy_url="https://example.com/privacy-v2",
    )
    changed_service = OnboardingService(repository, changed_documents)

    result = await changed_service.start(identity)

    assert result.view is OnboardingView.CONSENT_REQUIRED
    assert result.user.status is OnboardingStatus.AWAITING_CONSENT


async def test_decline_after_accept_does_not_revoke_current_consent(
    service: OnboardingService,
    repository: InMemoryOnboardingRepository,
    identity: TelegramIdentity,
) -> None:
    await service.accept(identity)

    result = await service.decline(identity)

    assert result.view is OnboardingView.MAIN_MENU
    assert len(repository.decision_details) == 1
    assert repository.decision_details[0][3] is ConsentDecision.ACCEPTED
