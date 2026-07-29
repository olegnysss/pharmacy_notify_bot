from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from pharmacy_bot.application.source_registry import SourceRegistryService
from pharmacy_bot.domain.adapter_contract import AdapterContractError
from pharmacy_bot.domain.source_integration import (
    CacheLookupStatus,
    IntegrationOutcome,
    IntegrationRequestFact,
    IntegrationRequestInput,
    SourceCacheKey,
    SourceCacheLookup,
    SourceCacheRecord,
    SourceHealth,
    SourceHealthPolicy,
    SourceHealthStatus,
    WebhookPayloadRejected,
    WebhookPolicy,
    WebhookReceipt,
    WebhookReceiveOutcome,
    WebhookReceiveResult,
    WebhookRejected,
    WebhookRejectionKind,
)
from pharmacy_bot.domain.source_registry import Source, SourceOperation

_DELIVERY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class WebhookReceiptRepository(Protocol):
    async def claim(
        self,
        source_id: int,
        delivery_key: str,
        body_digest: str,
        event_timestamp: datetime,
        body_bytes: int,
        *,
        received_at: datetime,
    ) -> tuple[WebhookReceipt, bool]: ...

    async def accept(
        self,
        receipt_id: int,
        business_fingerprint: str,
        *,
        completed_at: datetime,
    ) -> WebhookReceipt: ...

    async def quarantine(
        self,
        receipt_id: int,
        reason_code: str,
        *,
        completed_at: datetime,
    ) -> WebhookReceipt: ...


class AuthenticatedWebhookProcessor(Protocol):
    async def process(self, body: bytes, receipt: WebhookReceipt) -> str: ...


class SourceCacheRepository(Protocol):
    async def get(self, namespace_fingerprint: str) -> SourceCacheRecord | None: ...

    async def put(
        self,
        namespace_fingerprint: str,
        record: SourceCacheRecord,
    ) -> None: ...

    async def delete(self, namespace_fingerprint: str) -> None: ...

    async def invalidate_adapter(
        self,
        source_code: str,
        active_adapter_version: str,
    ) -> int: ...


class IntegrationObservabilityRepository(Protocol):
    async def record(
        self,
        value: IntegrationRequestInput,
        policy: SourceHealthPolicy,
    ) -> tuple[IntegrationRequestFact, SourceHealth]: ...


class HmacSha256WebhookAuthenticator:
    @staticmethod
    def sign(secret: bytes, timestamp: str, body: bytes) -> str:
        digest = hmac.new(
            secret,
            timestamp.encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={digest}"

    @classmethod
    def verify(
        cls,
        secret: bytes,
        timestamp: str,
        body: bytes,
        signature: str,
    ) -> bool:
        if len(secret) < 16 or not signature.startswith("sha256="):
            return False
        supplied = signature.removeprefix("sha256=").casefold()
        if re.fullmatch(r"[0-9a-f]{64}", supplied) is None:
            return False
        expected = cls.sign(secret, timestamp, body).removeprefix("sha256=")
        return hmac.compare_digest(expected, supplied)


class WebhookIntakeService:
    def __init__(
        self,
        repository: WebhookReceiptRepository,
        authenticator: HmacSha256WebhookAuthenticator | None = None,
    ) -> None:
        self._repository = repository
        self._authenticator = authenticator or HmacSha256WebhookAuthenticator()

    async def receive(
        self,
        source: Source,
        policy: WebhookPolicy,
        processor: AuthenticatedWebhookProcessor,
        *,
        secret: bytes,
        signature: str,
        timestamp: str,
        delivery_key: str,
        content_type: str,
        body: bytes,
        now: datetime,
    ) -> WebhookReceiveResult:
        self._validate_source(source)
        self._validate_policy(policy)
        self._utc(now)
        if (
            type(secret) is not bytes
            or type(signature) is not str
            or type(timestamp) is not str
            or type(delivery_key) is not str
            or type(content_type) is not str
            or type(body) is not bytes
        ):
            self._reject(
                WebhookRejectionKind.INVALID_SIGNATURE,
                "webhook authentication inputs are invalid",
            )
        if len(body) > policy.max_body_bytes:
            self._reject(
                WebhookRejectionKind.OVERSIZED,
                "webhook body exceeds policy limit",
            )
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            self._reject(
                WebhookRejectionKind.INVALID_CONTENT_TYPE,
                "webhook content type is invalid",
            )
        if _DELIVERY_KEY.fullmatch(delivery_key) is None:
            self._reject(
                WebhookRejectionKind.INVALID_DELIVERY_KEY,
                "webhook delivery key is invalid",
            )
        event_timestamp = self._event_timestamp(
            timestamp,
            now,
            policy,
        )
        if not self._authenticator.verify(secret, timestamp, body, signature):
            self._reject(
                WebhookRejectionKind.INVALID_SIGNATURE,
                "webhook signature is invalid",
            )
        body_digest = sha256(body).hexdigest()
        try:
            receipt, created = await self._repository.claim(
                source.id,
                delivery_key,
                body_digest,
                event_timestamp,
                len(body),
                received_at=now,
            )
        except WebhookRejected:
            raise
        if not created:
            if receipt.body_digest != body_digest:
                self._reject(
                    WebhookRejectionKind.REPLAY_CONFLICT,
                    "webhook delivery key was reused for another payload",
                )
            return WebhookReceiveResult(WebhookReceiveOutcome.DUPLICATE, receipt)
        try:
            business_fingerprint = await processor.process(body, receipt)
            if (
                type(business_fingerprint) is not str
                or _DIGEST.fullmatch(business_fingerprint) is None
            ):
                raise WebhookPayloadRejected("invalid_business_fingerprint")
        except asyncio.CancelledError:
            await asyncio.shield(
                self._repository.quarantine(
                    receipt.id,
                    "processing_cancelled",
                    completed_at=now,
                )
            )
            raise
        except WebhookPayloadRejected as error:
            quarantined = await self._repository.quarantine(
                receipt.id,
                self._reason(error.reason_code),
                completed_at=now,
            )
            return WebhookReceiveResult(
                WebhookReceiveOutcome.QUARANTINED,
                quarantined,
            )
        except AdapterContractError as error:
            quarantined = await self._repository.quarantine(
                receipt.id,
                self._reason(f"contract_{error.kind.value}"),
                completed_at=now,
            )
            return WebhookReceiveResult(
                WebhookReceiveOutcome.QUARANTINED,
                quarantined,
            )
        except (TypeError, UnicodeError, ValueError):
            quarantined = await self._repository.quarantine(
                receipt.id,
                "invalid_contract_event",
                completed_at=now,
            )
            return WebhookReceiveResult(
                WebhookReceiveOutcome.QUARANTINED,
                quarantined,
            )
        accepted = await self._repository.accept(
            receipt.id,
            business_fingerprint,
            completed_at=now,
        )
        return WebhookReceiveResult(WebhookReceiveOutcome.ACCEPTED, accepted)

    @staticmethod
    def _event_timestamp(
        raw: str,
        now: datetime,
        policy: WebhookPolicy,
    ) -> datetime:
        try:
            seconds = int(raw)
        except (TypeError, ValueError) as error:
            raise WebhookRejected(
                WebhookRejectionKind.INVALID_TIMESTAMP,
                "webhook timestamp is invalid",
            ) from error
        try:
            value = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise WebhookRejected(
                WebhookRejectionKind.INVALID_TIMESTAMP,
                "webhook timestamp is invalid",
            ) from error
        if value < now - timedelta(seconds=policy.replay_window_seconds) or value > now + timedelta(
            seconds=policy.future_skew_seconds
        ):
            raise WebhookRejected(
                WebhookRejectionKind.INVALID_TIMESTAMP,
                "webhook timestamp is outside replay window",
            )
        return value

    @staticmethod
    def _validate_source(source: Source) -> None:
        decision = SourceRegistryService.operation_decision(
            source.configuration,
            SourceOperation.RECEIVE_WEBHOOK,
        )
        if not decision.allowed:
            raise WebhookRejected(
                WebhookRejectionKind.INVALID_SIGNATURE,
                "webhook source is not permitted",
            )

    @staticmethod
    def _validate_policy(policy: WebhookPolicy) -> None:
        if not (
            type(policy.max_body_bytes) is int
            and type(policy.replay_window_seconds) is int
            and type(policy.future_skew_seconds) is int
            and 1 <= policy.max_body_bytes <= 10_000_000
            and 1 <= policy.replay_window_seconds <= 86_400
            and 0 <= policy.future_skew_seconds <= 3_600
        ):
            raise ValueError("webhook policy is invalid")

    @staticmethod
    def _reason(value: str) -> str:
        if _FAILURE_CODE.fullmatch(value) is None:
            return "invalid_contract_event"
        return value

    @staticmethod
    def _reject(kind: WebhookRejectionKind, message: str) -> None:
        raise WebhookRejected(kind, message)

    @staticmethod
    def _utc(value: datetime) -> None:
        offset = value.utcoffset() if type(value) is datetime else None
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("webhook timestamp must be UTC-aware")


class SourceResponseCache:
    def __init__(
        self,
        repository: SourceCacheRepository,
        *,
        max_payload_bytes: int = 1_000_000,
        max_ttl_seconds: int = 86_400,
    ) -> None:
        if max_payload_bytes <= 0 or max_ttl_seconds <= 0:
            raise ValueError("cache bounds are invalid")
        self._repository = repository
        self._max_payload_bytes = max_payload_bytes
        self._max_ttl_seconds = max_ttl_seconds

    async def get(
        self,
        key: SourceCacheKey,
        *,
        now: datetime,
    ) -> SourceCacheLookup:
        self._validate_key(key)
        self._utc(now)
        fingerprint = source_cache_fingerprint(key)
        record = await self._repository.get(fingerprint)
        if record is None:
            return SourceCacheLookup(CacheLookupStatus.MISS, None, None, None)
        if not self._valid_record(record, fingerprint, key, now):
            await self._repository.delete(fingerprint)
            return SourceCacheLookup(CacheLookupStatus.MISS, None, None, None)
        if now >= record.expires_at:
            await self._repository.delete(fingerprint)
            return SourceCacheLookup(
                CacheLookupStatus.STALE,
                None,
                record.created_at,
                record.expires_at,
            )
        return SourceCacheLookup(
            CacheLookupStatus.FRESH,
            record.payload,
            record.created_at,
            record.expires_at,
        )

    async def put(
        self,
        key: SourceCacheKey,
        payload: bytes,
        *,
        ttl_seconds: int,
        now: datetime,
    ) -> None:
        self._validate_key(key)
        self._utc(now)
        if (
            type(payload) is not bytes
            or len(payload) > self._max_payload_bytes
            or type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= self._max_ttl_seconds
        ):
            raise ValueError("cache value exceeds configured bounds")
        fingerprint = source_cache_fingerprint(key)
        await self._repository.put(
            fingerprint,
            SourceCacheRecord(
                fingerprint,
                key.source_code,
                key.adapter_version,
                payload,
                sha256(payload).hexdigest(),
                now,
                now + timedelta(seconds=ttl_seconds),
            ),
        )

    async def invalidate_adapter(
        self,
        source_code: str,
        active_adapter_version: str,
    ) -> int:
        self._namespace(source_code, "source code")
        self._version(active_adapter_version)
        return await self._repository.invalidate_adapter(
            source_code,
            active_adapter_version,
        )

    def _valid_record(
        self,
        record: SourceCacheRecord,
        fingerprint: str,
        key: SourceCacheKey,
        now: datetime,
    ) -> bool:
        if type(record.created_at) is not datetime or type(record.expires_at) is not datetime:
            return False
        created_offset = record.created_at.utcoffset()
        expires_offset = record.expires_at.utcoffset()
        return bool(
            record.namespace_fingerprint == fingerprint
            and record.source_code == key.source_code
            and record.adapter_version == key.adapter_version
            and type(record.payload) is bytes
            and len(record.payload) <= self._max_payload_bytes
            and type(record.payload_digest) is str
            and _DIGEST.fullmatch(record.payload_digest) is not None
            and sha256(record.payload).hexdigest() == record.payload_digest
            and created_offset is not None
            and created_offset.total_seconds() == 0
            and expires_offset is not None
            and expires_offset.total_seconds() == 0
            and record.expires_at > record.created_at
            and record.expires_at - record.created_at <= timedelta(seconds=self._max_ttl_seconds)
            and now >= record.created_at
        )

    @classmethod
    def _validate_key(cls, key: SourceCacheKey) -> None:
        if type(key) is not SourceCacheKey or not isinstance(key.operation, SourceOperation):
            raise ValueError("cache key is invalid")
        cls._namespace(key.source_code, "source code")
        if key.region_key is not None:
            cls._namespace(key.region_key, "region key")
        if key.user_scope_key is not None:
            cls._namespace(key.user_scope_key, "user scope key")
        cls._version(key.schema_version)
        cls._version(key.adapter_version)

    @staticmethod
    def _namespace(value: str, label: str) -> None:
        if type(value) is not str or _NAMESPACE.fullmatch(value) is None:
            raise ValueError(f"cache {label} is invalid")

    @staticmethod
    def _version(value: str) -> None:
        if type(value) is not str or _VERSION.fullmatch(value) is None:
            raise ValueError("cache version is invalid")

    @staticmethod
    def _utc(value: datetime) -> None:
        offset = value.utcoffset() if type(value) is datetime else None
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("cache timestamp must be UTC-aware")


class IntegrationObservabilityService:
    def __init__(
        self,
        repository: IntegrationObservabilityRepository,
        policy: SourceHealthPolicy,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._validate_policy(policy)

    async def record(
        self,
        value: IntegrationRequestInput,
    ) -> tuple[IntegrationRequestFact, SourceHealth]:
        self._validate(value)
        return await self._repository.record(value, self._policy)

    @staticmethod
    def _validate(value: IntegrationRequestInput) -> None:
        if (
            type(value) is not IntegrationRequestInput
            or type(value.source_id) is not int
            or value.source_id <= 0
        ):
            raise ValueError("integration request metadata is invalid")
        try:
            correlation = UUID(value.correlation_id)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("integration correlation id is invalid") from error
        if str(correlation) != value.correlation_id:
            raise ValueError("integration correlation id is invalid")
        offset = value.occurred_at.utcoffset() if type(value.occurred_at) is datetime else None
        if (
            not isinstance(value.operation, SourceOperation)
            or not isinstance(value.outcome, IntegrationOutcome)
            or offset is None
            or offset.total_seconds() != 0
            or type(value.duration_ms) is not int
            or not 0 <= value.duration_ms <= 86_400_000
            or type(value.attempts) is not int
            or not 1 <= value.attempts <= 100
            or type(value.response_bytes) is not int
            or not 0 <= value.response_bytes <= 100_000_000
            or (
                value.http_status is not None
                and (type(value.http_status) is not int or not 100 <= value.http_status <= 599)
            )
            or (
                value.cache_status is not None
                and not isinstance(value.cache_status, CacheLookupStatus)
            )
            or (
                value.failure_code is not None
                and (
                    type(value.failure_code) is not str
                    or _FAILURE_CODE.fullmatch(value.failure_code) is None
                )
            )
        ):
            raise ValueError("integration request metadata is invalid")

    @staticmethod
    def _validate_policy(value: SourceHealthPolicy) -> None:
        if not (
            type(value.degrade_after_failures) is int
            and type(value.recover_after_successes) is int
            and type(value.retained_requests_per_source) is int
            and type(value.retained_transitions_per_source) is int
            and 1 <= value.degrade_after_failures <= 100
            and 1 <= value.recover_after_successes <= 100
            and 1 <= value.retained_requests_per_source <= 100_000
            and 1 <= value.retained_transitions_per_source <= 10_000
        ):
            raise ValueError("source health policy is invalid")


def source_cache_fingerprint(value: SourceCacheKey) -> str:
    payload = {
        "source_code": value.source_code,
        "operation": value.operation.value,
        "region_key": value.region_key,
        "user_scope_key": value.user_scope_key,
        "schema_version": value.schema_version,
        "adapter_version": value.adapter_version,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evolve_source_health(
    current: SourceHealth,
    outcome: IntegrationOutcome,
    policy: SourceHealthPolicy,
    *,
    now: datetime,
) -> SourceHealth:
    status = current.status
    failures = current.consecutive_failures
    successes = current.consecutive_successes
    version = current.version
    changed_at = current.changed_at
    if outcome.source_failure:
        failures += 1
        successes = 0
        if status is SourceHealthStatus.HEALTHY and failures >= policy.degrade_after_failures:
            status = SourceHealthStatus.DEGRADED
            version += 1
            changed_at = now
    elif outcome is IntegrationOutcome.SUCCESS:
        failures = 0
        if status is SourceHealthStatus.DEGRADED:
            successes += 1
            if successes >= policy.recover_after_successes:
                status = SourceHealthStatus.HEALTHY
                failures = 0
                successes = 0
                version += 1
                changed_at = now
        else:
            successes = 0
    return SourceHealth(
        current.source_id,
        status,
        failures,
        successes,
        version,
        changed_at,
        now,
    )
