from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pharmacy_bot.domain.source_registry import SourceOperation


class HttpMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"

    @property
    def retry_safe(self) -> bool:
        return self in {HttpMethod.GET, HttpMethod.HEAD}


class TransportFailureKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    CLIENT = "client"
    UPSTREAM = "upstream"
    NETWORK = "network"
    TIMEOUT = "timeout"
    SCHEMA = "schema"
    POLICY = "policy"
    CIRCUIT_OPEN = "circuit_open"


@dataclass(frozen=True, slots=True)
class TransportPolicy:
    connect_timeout_seconds: float
    read_timeout_seconds: float
    total_timeout_seconds: float
    max_attempts: int
    base_backoff_seconds: float
    max_backoff_seconds: float
    jitter_ratio: float
    max_retry_after_seconds: float
    max_response_bytes: int
    allowed_content_types: frozenset[str]
    max_redirects: int
    breaker_failure_threshold: int
    breaker_window_seconds: float
    breaker_recovery_seconds: float


@dataclass(frozen=True, slots=True)
class TransportRequest:
    source_code: str
    operation: SourceOperation
    method: HttpMethod
    url: str
    headers: Mapping[str, str]
    body: bytes | None = None


class WireBody(Protocol):
    def __aiter__(self) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class WireResponse:
    status: int
    headers: Mapping[str, str]
    content_type: str | None
    body: WireBody


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    content_type: str
    body: bytes
    attempts: int
    final_url: str


@dataclass(frozen=True, slots=True)
class SafeTransportDiagnostic:
    source_code: str
    operation: str
    method: str
    scheme: str
    host: str
    path: str
    attempt: int
    outcome: str


class TransportFailure(Exception):
    def __init__(
        self,
        kind: TransportFailureKind,
        message: str,
        *,
        status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after_seconds = retry_after_seconds


class WireNetworkError(Exception):
    pass


class WireTimeoutError(Exception):
    pass
