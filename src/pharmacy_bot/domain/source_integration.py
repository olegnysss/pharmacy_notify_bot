from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pharmacy_bot.domain.source_registry import SourceOperation


class WebhookReceiptStatus(StrEnum):
    PROCESSING = "processing"
    ACCEPTED = "accepted"
    QUARANTINED = "quarantined"


class WebhookReceiveOutcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    QUARANTINED = "quarantined"


class WebhookRejectionKind(StrEnum):
    OVERSIZED = "oversized"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INVALID_SIGNATURE = "invalid_signature"
    INVALID_DELIVERY_KEY = "invalid_delivery_key"
    REPLAY_CONFLICT = "replay_conflict"


@dataclass(frozen=True, slots=True)
class WebhookPolicy:
    max_body_bytes: int
    replay_window_seconds: int
    future_skew_seconds: int


@dataclass(frozen=True, slots=True)
class WebhookReceipt:
    id: int
    source_id: int
    delivery_key: str
    body_digest: str
    event_timestamp: datetime
    body_bytes: int
    status: WebhookReceiptStatus
    business_fingerprint: str | None
    quarantine_reason: str | None
    received_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class WebhookReceiveResult:
    outcome: WebhookReceiveOutcome
    receipt: WebhookReceipt


class CacheLookupStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISS = "miss"


@dataclass(frozen=True, slots=True)
class SourceCacheKey:
    source_code: str
    operation: SourceOperation
    region_key: str | None
    user_scope_key: str | None
    schema_version: str
    adapter_version: str


@dataclass(frozen=True, slots=True)
class SourceCacheRecord:
    namespace_fingerprint: str
    source_code: str
    adapter_version: str
    payload: bytes
    payload_digest: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SourceCacheLookup:
    status: CacheLookupStatus
    payload: bytes | None
    created_at: datetime | None
    expires_at: datetime | None


class IntegrationOutcome(StrEnum):
    SUCCESS = "success"
    CLIENT_FAILURE = "client_failure"
    UPSTREAM_FAILURE = "upstream_failure"
    NETWORK_FAILURE = "network_failure"
    CONTRACT_FAILURE = "contract_failure"
    POLICY_REJECTION = "policy_rejection"

    @property
    def source_failure(self) -> bool:
        return self in {
            IntegrationOutcome.UPSTREAM_FAILURE,
            IntegrationOutcome.NETWORK_FAILURE,
            IntegrationOutcome.CONTRACT_FAILURE,
        }


class SourceHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class IntegrationRequestInput:
    source_id: int
    correlation_id: str
    operation: SourceOperation
    outcome: IntegrationOutcome
    duration_ms: int
    attempts: int
    response_bytes: int
    http_status: int | None
    cache_status: CacheLookupStatus | None
    failure_code: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class IntegrationRequestFact:
    id: int
    source_id: int
    correlation_id: str
    operation: SourceOperation
    outcome: IntegrationOutcome
    duration_ms: int
    attempts: int
    response_bytes: int
    http_status: int | None
    cache_status: CacheLookupStatus | None
    failure_code: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source_id: int
    status: SourceHealthStatus
    consecutive_failures: int
    consecutive_successes: int
    version: int
    changed_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourceHealthPolicy:
    degrade_after_failures: int
    recover_after_successes: int
    retained_requests_per_source: int
    retained_transitions_per_source: int


class WebhookRejected(Exception):
    def __init__(self, kind: WebhookRejectionKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class WebhookPayloadRejected(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__("authenticated webhook payload was rejected")
        self.reason_code = reason_code
