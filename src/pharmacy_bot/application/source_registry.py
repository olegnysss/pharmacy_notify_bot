from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from pharmacy_bot.domain.source_registry import (
    LegalUsageStatus,
    Source,
    SourceConfiguration,
    SourceLimits,
    SourceOperation,
    SourceOperationDecision,
    SourceRegistryConflict,
    SourceStatus,
    SourceType,
)

_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_VERSION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class SourceRegistryRepository(Protocol):
    async def create_or_get(
        self,
        configuration: SourceConfiguration,
        fingerprint: str,
        *,
        now: datetime,
    ) -> Source: ...

    async def revise(
        self,
        source_id: int,
        expected_version: int,
        configuration: SourceConfiguration,
        fingerprint: str,
        *,
        actor_internal_id: int,
        reason: str,
        now: datetime,
    ) -> Source: ...


class SourceRegistryService:
    def __init__(self, repository: SourceRegistryRepository) -> None:
        self._repository = repository

    def normalize(self, raw: SourceConfiguration) -> SourceConfiguration:
        if not isinstance(raw.source_type, SourceType):
            raise SourceRegistryConflict("source type is invalid")
        if not isinstance(raw.status, SourceStatus):
            raise SourceRegistryConflict("source status is invalid")
        if not isinstance(raw.legal_status, LegalUsageStatus):
            raise SourceRegistryConflict("source legal status is invalid")
        code = raw.code.strip().casefold()
        name = unicodedata.normalize("NFKC", raw.name).strip()
        if not _CODE.fullmatch(code) or not 2 <= len(name) <= 256:
            raise SourceRegistryConflict("source identity is invalid")
        if not _VERSION.fullmatch(raw.adapter_version) or not _VERSION.fullmatch(
            raw.capability_version
        ):
            raise SourceRegistryConflict("source version is invalid")
        base_urls = tuple(sorted({self._base_url(value) for value in raw.base_urls}))
        redirect_hosts = tuple(
            sorted({self._host(value, "redirect host") for value in raw.redirect_hosts})
        )
        if raw.source_type is not SourceType.MANUAL and not base_urls:
            raise SourceRegistryConflict("network source requires an HTTPS base URL")
        self._validate_limits(raw.limits)
        if any(not isinstance(item, SourceOperation) for item in raw.capabilities):
            raise SourceRegistryConflict("source capability is invalid")
        capabilities = frozenset(raw.capabilities)
        if not capabilities:
            raise SourceRegistryConflict("source capabilities cannot be empty")
        if SourceOperation.RECEIVE_WEBHOOK in capabilities and raw.source_type not in {
            SourceType.WEBHOOK,
            SourceType.PARTNER_API,
        }:
            raise SourceRegistryConflict("webhook capability is incompatible with source type")
        if SourceOperation.IMPORT_EXPORT in capabilities and raw.source_type not in {
            SourceType.EXPORT,
            SourceType.PARTNER_API,
        }:
            raise SourceRegistryConflict("export capability is incompatible with source type")
        return SourceConfiguration(
            code,
            name,
            raw.source_type,
            raw.status,
            raw.legal_status,
            raw.adapter_version,
            raw.capability_version,
            capabilities,
            base_urls,
            redirect_hosts,
            raw.limits,
        )

    async def create_or_get(
        self,
        raw: SourceConfiguration,
        *,
        now: datetime,
    ) -> Source:
        value = self.normalize(raw)
        return await self._repository.create_or_get(
            value,
            self.fingerprint(value),
            now=now,
        )

    async def revise(
        self,
        source_id: int,
        expected_version: int,
        raw: SourceConfiguration,
        *,
        actor_internal_id: int,
        reason: str,
        now: datetime,
    ) -> Source:
        audit_reason = unicodedata.normalize("NFKC", reason).strip()
        if actor_internal_id <= 0 or not 3 <= len(audit_reason) <= 128:
            raise SourceRegistryConflict("source change audit context is invalid")
        value = self.normalize(raw)
        return await self._repository.revise(
            source_id,
            expected_version,
            value,
            self.fingerprint(value),
            actor_internal_id=actor_internal_id,
            reason=audit_reason,
            now=now,
        )

    @staticmethod
    def operation_decision(
        configuration: SourceConfiguration,
        operation: SourceOperation,
    ) -> SourceOperationDecision:
        reasons = []
        if configuration.status is not SourceStatus.ACTIVE:
            reasons.append(f"source_status:{configuration.status.value}")
        if configuration.legal_status is not LegalUsageStatus.ALLOWED:
            reasons.append(f"legal_status:{configuration.legal_status.value}")
        if operation not in configuration.capabilities:
            reasons.append("capability_not_declared")
        return SourceOperationDecision(not reasons, tuple(reasons))

    @staticmethod
    def host_allowed(configuration: SourceConfiguration, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            return False
        trusted = {
            *(urlsplit(item).hostname for item in configuration.base_urls),
            *configuration.redirect_hosts,
        }
        return bool(
            parsed.scheme == "https"
            and host in trusted
            and parsed.username is None
            and parsed.password is None
            and port in {None, 443}
        )

    @staticmethod
    def fingerprint(value: SourceConfiguration) -> str:
        payload = asdict(value)
        payload["source_type"] = value.source_type.value
        payload["status"] = value.status.value
        payload["legal_status"] = value.legal_status.value
        payload["capabilities"] = sorted(item.value for item in value.capabilities)
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _base_url(value: str) -> str:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise SourceRegistryConflict("source base URL is invalid") from error
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.query
            or parsed.fragment
        ):
            raise SourceRegistryConflict("source base URL is invalid")
        normalized_host = SourceRegistryService._host(host, "source base URL host")
        return urlunsplit(("https", normalized_host, parsed.path.rstrip("/") or "", "", ""))

    @staticmethod
    def _host(value: str, label: str) -> str:
        candidate = value.strip().rstrip(".").casefold()
        try:
            normalized = candidate.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise SourceRegistryConflict(f"{label} is invalid") from error
        labels = normalized.split(".")
        if not normalized or len(normalized) > 253 or len(labels) < 2:
            raise SourceRegistryConflict(f"{label} is invalid")
        if any(
            not part
            or len(part) > 63
            or part.startswith("-")
            or part.endswith("-")
            or re.fullmatch(r"[a-z0-9-]+", part) is None
            for part in labels
        ):
            raise SourceRegistryConflict(f"{label} is invalid")
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            pass
        else:
            raise SourceRegistryConflict(f"{label} is invalid")
        return normalized

    @staticmethod
    def _validate_limits(value: SourceLimits) -> None:
        if not (
            1 <= value.requests_per_window <= 1_000_000
            and 1 <= value.window_seconds <= 86_400
            and 1 <= value.max_concurrency <= 1_000
            and 1 <= value.freshness_seconds <= 604_800
            and 0 <= value.cache_ttl_seconds <= value.freshness_seconds
        ):
            raise SourceRegistryConflict("source operational limits are invalid")
