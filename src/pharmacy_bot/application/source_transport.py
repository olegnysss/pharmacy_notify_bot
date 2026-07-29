from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

from pharmacy_bot.application.source_registry import SourceRegistryService
from pharmacy_bot.domain.source_registry import SourceConfiguration
from pharmacy_bot.domain.source_transport import (
    HttpMethod,
    SafeTransportDiagnostic,
    TransportFailure,
    TransportFailureKind,
    TransportPolicy,
    TransportRequest,
    TransportResponse,
    WireNetworkError,
    WireResponse,
    WireTimeoutError,
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization", "set-cookie"})


class MonotonicClock(Protocol):
    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class RandomSource(Protocol):
    def uniform(self, lower: float, upper: float) -> float: ...


class WireTransport(Protocol):
    """HTTPS-only wire port.

    Implementations must verify the peer certificate, disable automatic redirects,
    avoid persistent cookies, and stream the response without implicit decompression.
    """

    async def request(
        self,
        *,
        method: HttpMethod,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        total_timeout_seconds: float,
    ) -> WireResponse: ...


class DiagnosticSink(Protocol):
    def record(self, value: SafeTransportDiagnostic) -> None: ...


class NullDiagnosticSink:
    def record(self, value: SafeTransportDiagnostic) -> None:
        del value


@dataclass(slots=True)
class _TokenBucket:
    capacity: float
    refill_per_second: float
    tokens: float
    updated_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def wait(
        self,
        *,
        clock: MonotonicClock,
        deadline: float,
    ) -> None:
        while True:
            async with self.lock:
                now = clock.monotonic()
                elapsed = max(0.0, now - self.updated_at)
                self.tokens = min(
                    self.capacity,
                    self.tokens + elapsed * self.refill_per_second,
                )
                self.updated_at = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                delay = (1 - self.tokens) / self.refill_per_second
            if now + delay > deadline:
                raise TransportFailure(
                    TransportFailureKind.RATE_LIMITED,
                    "source rate limit exceeds request deadline",
                )
            await clock.sleep(delay)


@dataclass(slots=True)
class _CircuitBreaker:
    failure_threshold: int
    window_seconds: float
    recovery_seconds: float
    failures: deque[float] = field(default_factory=deque)
    opened_at: float | None = None
    probe_in_flight: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def enter(self, now: float) -> bool:
        async with self.lock:
            self._discard_old(now)
            if self.opened_at is None:
                return True
            if now - self.opened_at < self.recovery_seconds:
                return False
            if self.probe_in_flight:
                return False
            self.probe_in_flight = True
            return True

    async def success(self) -> None:
        async with self.lock:
            self.failures.clear()
            self.opened_at = None
            self.probe_in_flight = False

    async def failure(self, now: float) -> None:
        async with self.lock:
            self.probe_in_flight = False
            self._discard_old(now)
            self.failures.append(now)
            if self.opened_at is not None or len(self.failures) >= self.failure_threshold:
                self.opened_at = now

    async def release_probe(self) -> None:
        async with self.lock:
            self.probe_in_flight = False

    def _discard_old(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.failures and self.failures[0] < cutoff:
            self.failures.popleft()


@dataclass(slots=True)
class _SourceRuntime:
    fingerprint: tuple[int, int, int, float, float, float]
    bucket: _TokenBucket
    semaphore: asyncio.Semaphore
    breaker: _CircuitBreaker


class SourceTransport:
    def __init__(
        self,
        wire: WireTransport,
        clock: MonotonicClock,
        random: RandomSource,
        *,
        diagnostics: DiagnosticSink | None = None,
    ) -> None:
        self._wire = wire
        self._clock = clock
        self._random = random
        self._diagnostics = diagnostics or NullDiagnosticSink()
        self._runtimes: dict[str, _SourceRuntime] = {}
        self._runtime_lock = asyncio.Lock()

    async def execute(
        self,
        source: SourceConfiguration,
        policy: TransportPolicy,
        request: TransportRequest,
    ) -> TransportResponse:
        self._validate(source, policy, request)
        decision = SourceRegistryService.operation_decision(source, request.operation)
        if not decision.allowed:
            raise TransportFailure(
                TransportFailureKind.POLICY,
                "source operation is not permitted",
            )
        runtime = await self._runtime(source, policy)
        deadline = self._clock.monotonic() + policy.total_timeout_seconds
        await self._acquire_concurrency(runtime.semaphore, deadline)
        entered_breaker = False
        try:
            if not await runtime.breaker.enter(self._clock.monotonic()):
                raise TransportFailure(
                    TransportFailureKind.CIRCUIT_OPEN,
                    "source circuit is open",
                )
            entered_breaker = True
            response = await self._attempts(source, policy, request, runtime, deadline)
            await runtime.breaker.success()
            return response
        except TransportFailure as error:
            if entered_breaker:
                if self._counts_for_breaker(error.kind):
                    await runtime.breaker.failure(self._clock.monotonic())
                else:
                    await runtime.breaker.release_probe()
            raise
        except asyncio.CancelledError:
            if entered_breaker:
                await runtime.breaker.release_probe()
            raise
        except Exception:
            if entered_breaker:
                await runtime.breaker.release_probe()
            raise
        finally:
            runtime.semaphore.release()

    async def _attempts(
        self,
        source: SourceConfiguration,
        policy: TransportPolicy,
        request: TransportRequest,
        runtime: _SourceRuntime,
        deadline: float,
    ) -> TransportResponse:
        current_url = request.url
        current_method = request.method
        current_headers = dict(request.headers)
        current_body = request.body
        redirects = 0
        attempt = 0
        while True:
            attempt += 1
            self._require_time(deadline)
            await runtime.bucket.wait(clock=self._clock, deadline=deadline)
            try:
                raw = await self._wire.request(
                    method=current_method,
                    url=current_url,
                    headers=current_headers,
                    body=current_body,
                    connect_timeout_seconds=min(
                        policy.connect_timeout_seconds,
                        self._remaining(deadline),
                    ),
                    read_timeout_seconds=min(
                        policy.read_timeout_seconds,
                        self._remaining(deadline),
                    ),
                    total_timeout_seconds=self._remaining(deadline),
                )
            except WireTimeoutError as error:
                failure = TransportFailure(
                    TransportFailureKind.TIMEOUT,
                    "source request timed out",
                )
                self._record(request, current_method, current_url, attempt, "timeout")
                if await self._retry(failure, current_method, attempt, policy, deadline):
                    continue
                raise failure from error
            except WireNetworkError as error:
                failure = TransportFailure(
                    TransportFailureKind.NETWORK,
                    "source network request failed",
                )
                self._record(request, current_method, current_url, attempt, "network")
                if await self._retry(failure, current_method, attempt, policy, deadline):
                    continue
                raise failure from error

            if raw.status in _REDIRECT_STATUSES:
                location = self._header(raw.headers, "location")
                if location is None or redirects >= policy.max_redirects:
                    await self._close_response(raw)
                    raise TransportFailure(
                        TransportFailureKind.POLICY,
                        "source redirect policy rejected response",
                        status=raw.status,
                    )
                redirected = urljoin(current_url, location)
                if not SourceRegistryService.host_allowed(source, redirected):
                    await self._close_response(raw)
                    raise TransportFailure(
                        TransportFailureKind.POLICY,
                        "source redirect host is not trusted",
                        status=raw.status,
                    )
                if not current_method.retry_safe and raw.status != 303:
                    await self._close_response(raw)
                    raise TransportFailure(
                        TransportFailureKind.POLICY,
                        "unsafe source operation cannot be redirected",
                        status=raw.status,
                    )
                if urlsplit(redirected).hostname != urlsplit(current_url).hostname:
                    current_headers = {
                        key: value
                        for key, value in current_headers.items()
                        if key.casefold() not in _SENSITIVE_HEADERS
                    }
                if raw.status == 303:
                    current_method = HttpMethod.GET
                    current_body = None
                current_url = redirected
                redirects += 1
                attempt -= 1
                await self._close_response(raw)
                continue

            status_failure = self._status_failure(raw)
            if status_failure is not None:
                await self._close_response(raw)
                self._record(
                    request,
                    current_method,
                    current_url,
                    attempt,
                    status_failure.kind.value,
                )
                if await self._retry(
                    status_failure,
                    current_method,
                    attempt,
                    policy,
                    deadline,
                ):
                    continue
                raise status_failure

            content_type = self._content_type(raw.content_type)
            content_encoding = self._header(raw.headers, "content-encoding")
            if content_encoding is not None and content_encoding.casefold() != "identity":
                await self._close_response(raw)
                raise TransportFailure(
                    TransportFailureKind.POLICY,
                    "compressed source response is not allowed",
                    status=raw.status,
                )
            if content_type not in policy.allowed_content_types:
                await self._close_response(raw)
                raise TransportFailure(
                    TransportFailureKind.SCHEMA,
                    "source response content type is not allowed",
                    status=raw.status,
                )
            body = await self._bounded_body(raw, policy.max_response_bytes, deadline)
            self._record(request, current_method, current_url, attempt, "success")
            return TransportResponse(
                raw.status,
                content_type,
                body,
                attempt,
                self._safe_url(current_url),
            )

    async def _retry(
        self,
        failure: TransportFailure,
        method: HttpMethod,
        attempt: int,
        policy: TransportPolicy,
        deadline: float,
    ) -> bool:
        if (
            not method.retry_safe
            or failure.kind
            not in {
                TransportFailureKind.RATE_LIMITED,
                TransportFailureKind.UPSTREAM,
                TransportFailureKind.NETWORK,
                TransportFailureKind.TIMEOUT,
            }
            or attempt >= policy.max_attempts
        ):
            return False
        if (
            failure.kind is TransportFailureKind.UPSTREAM
            and failure.status not in _RETRYABLE_STATUSES
        ):
            return False
        exponential = min(
            policy.max_backoff_seconds,
            policy.base_backoff_seconds * (2 ** (attempt - 1)),
        )
        if failure.retry_after_seconds is not None:
            exponential = min(
                policy.max_retry_after_seconds,
                max(exponential, failure.retry_after_seconds),
            )
        spread = exponential * policy.jitter_ratio
        delay = max(0.0, self._random.uniform(exponential - spread, exponential + spread))
        if self._clock.monotonic() + delay >= deadline:
            return False
        await self._clock.sleep(delay)
        return True

    async def _runtime(
        self,
        source: SourceConfiguration,
        policy: TransportPolicy,
    ) -> _SourceRuntime:
        limits = source.limits
        fingerprint = (
            limits.requests_per_window,
            limits.window_seconds,
            limits.max_concurrency,
            policy.breaker_failure_threshold,
            policy.breaker_window_seconds,
            policy.breaker_recovery_seconds,
        )
        async with self._runtime_lock:
            existing = self._runtimes.get(source.code)
            if existing is not None and existing.fingerprint == fingerprint:
                return existing
            now = self._clock.monotonic()
            runtime = _SourceRuntime(
                fingerprint,
                _TokenBucket(
                    float(limits.requests_per_window),
                    limits.requests_per_window / limits.window_seconds,
                    float(limits.requests_per_window),
                    now,
                ),
                asyncio.Semaphore(limits.max_concurrency),
                _CircuitBreaker(
                    policy.breaker_failure_threshold,
                    policy.breaker_window_seconds,
                    policy.breaker_recovery_seconds,
                ),
            )
            self._runtimes[source.code] = runtime
            return runtime

    async def _acquire_concurrency(
        self,
        semaphore: asyncio.Semaphore,
        deadline: float,
    ) -> None:
        remaining = self._remaining(deadline)
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=remaining)
        except TimeoutError as error:
            raise TransportFailure(
                TransportFailureKind.TIMEOUT,
                "source concurrency wait exceeded request deadline",
            ) from error

    async def _bounded_body(
        self,
        response: WireResponse,
        limit: int,
        deadline: float,
    ) -> bytes:
        try:
            length = self._header(response.headers, "content-length")
            if length is not None:
                try:
                    declared = int(length)
                except ValueError as error:
                    raise TransportFailure(
                        TransportFailureKind.SCHEMA,
                        "source content length is invalid",
                    ) from error
                if declared < 0 or declared > limit:
                    raise TransportFailure(
                        TransportFailureKind.POLICY,
                        "source response exceeds byte limit",
                    )
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.body:
                self._require_time(deadline)
                size += len(chunk)
                if size > limit:
                    raise TransportFailure(
                        TransportFailureKind.POLICY,
                        "source response exceeds byte limit",
                    )
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            await self._close_response(response)

    @staticmethod
    async def _close_response(response: WireResponse) -> None:
        try:
            await response.body.aclose()
        except Exception:
            pass

    @staticmethod
    def _status_failure(response: WireResponse) -> TransportFailure | None:
        if 200 <= response.status < 300:
            return None
        if response.status in _RETRYABLE_STATUSES:
            kind = (
                TransportFailureKind.RATE_LIMITED
                if response.status == 429
                else TransportFailureKind.UPSTREAM
            )
            return TransportFailure(
                kind,
                "source returned a transient error",
                status=response.status,
                retry_after_seconds=(
                    SourceTransport._retry_after(response.headers)
                    if response.status == 429
                    else None
                ),
            )
        if 400 <= response.status < 500:
            return TransportFailure(
                TransportFailureKind.CLIENT,
                "source rejected request",
                status=response.status,
            )
        return TransportFailure(
            TransportFailureKind.UPSTREAM,
            "source returned an upstream error",
            status=response.status,
        )

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float | None:
        value = SourceTransport._header(headers, "retry-after")
        if value is None:
            return None
        try:
            seconds = float(value)
        except ValueError:
            return None
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    @staticmethod
    def _content_type(value: str | None) -> str:
        if value is None:
            return ""
        message = Message()
        message["content-type"] = value
        return message.get_content_type().casefold()

    def _record(
        self,
        request: TransportRequest,
        method: HttpMethod,
        url: str,
        attempt: int,
        outcome: str,
    ) -> None:
        parsed = urlsplit(url)
        try:
            self._diagnostics.record(
                SafeTransportDiagnostic(
                    request.source_code,
                    request.operation.value,
                    method.value,
                    parsed.scheme,
                    parsed.hostname or "",
                    parsed.path,
                    attempt,
                    outcome,
                )
            )
        except Exception:
            pass

    @staticmethod
    def _safe_url(value: str) -> str:
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _remaining(self, deadline: float) -> float:
        return max(0.0, deadline - self._clock.monotonic())

    def _require_time(self, deadline: float) -> None:
        if self._clock.monotonic() >= deadline:
            raise TransportFailure(
                TransportFailureKind.TIMEOUT,
                "source request exceeded total timeout",
            )

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        normalized = name.casefold()
        return next(
            (value for key, value in headers.items() if key.casefold() == normalized),
            None,
        )

    @staticmethod
    def _counts_for_breaker(kind: TransportFailureKind) -> bool:
        return kind in {
            TransportFailureKind.UPSTREAM,
            TransportFailureKind.NETWORK,
            TransportFailureKind.TIMEOUT,
            TransportFailureKind.SCHEMA,
        }

    @staticmethod
    def _validate(
        source: SourceConfiguration,
        policy: TransportPolicy,
        request: TransportRequest,
    ) -> None:
        if request.source_code != source.code:
            raise TransportFailure(
                TransportFailureKind.POLICY,
                "request source does not match registered source",
            )
        if not SourceRegistryService.host_allowed(source, request.url):
            raise TransportFailure(
                TransportFailureKind.POLICY,
                "request URL is not trusted",
            )
        if urlsplit(request.url).fragment:
            raise TransportFailure(
                TransportFailureKind.POLICY,
                "request URL fragment is not allowed",
            )
        if any(
            not key or "\r" in key or "\n" in key or "\r" in value or "\n" in value
            for key, value in request.headers.items()
        ):
            raise TransportFailure(
                TransportFailureKind.POLICY,
                "request headers are invalid",
            )
        time_values = (
            policy.connect_timeout_seconds,
            policy.read_timeout_seconds,
            policy.total_timeout_seconds,
            policy.base_backoff_seconds,
            policy.max_backoff_seconds,
            policy.jitter_ratio,
            policy.max_retry_after_seconds,
            policy.breaker_window_seconds,
            policy.breaker_recovery_seconds,
        )
        if (
            not all(math.isfinite(item) for item in time_values)
            or policy.connect_timeout_seconds <= 0
            or policy.read_timeout_seconds <= 0
            or policy.total_timeout_seconds <= 0
            or policy.connect_timeout_seconds > policy.total_timeout_seconds
            or policy.read_timeout_seconds > policy.total_timeout_seconds
            or not 1 <= policy.max_attempts <= 10
            or policy.base_backoff_seconds < 0
            or policy.max_backoff_seconds < policy.base_backoff_seconds
            or not 0 <= policy.jitter_ratio <= 1
            or policy.max_retry_after_seconds < 0
            or not 1 <= policy.max_response_bytes <= 100_000_000
            or not policy.allowed_content_types
            or any(
                item != item.casefold() or "/" not in item or ";" in item
                for item in policy.allowed_content_types
            )
            or not 0 <= policy.max_redirects <= 10
            or not 1 <= policy.breaker_failure_threshold <= 1_000
            or policy.breaker_window_seconds <= 0
            or policy.breaker_recovery_seconds <= 0
        ):
            raise TransportFailure(
                TransportFailureKind.POLICY,
                "transport policy is invalid",
            )
