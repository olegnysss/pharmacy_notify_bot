from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace

import pytest

from pharmacy_bot.application.source_transport import SourceTransport
from pharmacy_bot.domain.source_registry import (
    LegalUsageStatus,
    SourceConfiguration,
    SourceLimits,
    SourceOperation,
    SourceStatus,
    SourceType,
)
from pharmacy_bot.domain.source_transport import (
    HttpMethod,
    SafeTransportDiagnostic,
    TransportFailure,
    TransportFailureKind,
    TransportPolicy,
    TransportRequest,
    WireNetworkError,
    WireResponse,
    WireTimeoutError,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds
        await asyncio.sleep(0)


class FixedRandom:
    def uniform(self, lower: float, upper: float) -> float:
        del upper
        return lower


class Diagnostics:
    def __init__(self) -> None:
        self.values: list[SafeTransportDiagnostic] = []

    def record(self, value: SafeTransportDiagnostic) -> None:
        self.values.append(value)


async def chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


def response(
    status: int = 200,
    *,
    headers: Mapping[str, str] | None = None,
    content_type: str | None = "application/json; charset=utf-8",
    values: tuple[bytes, ...] = (b"{}",),
) -> WireResponse:
    return WireResponse(status, headers or {}, content_type, chunks(*values))


class FakeWire:
    def __init__(self, *results: WireResponse | Exception) -> None:
        self.results = deque(results)
        self.calls: list[dict[str, object]] = []

    async def request(self, **values: object) -> WireResponse:
        self.calls.append(values)
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


def source(
    code: str = "source-a",
    *,
    host: str = "api-a.example",
    limits: SourceLimits | None = None,
    status: SourceStatus = SourceStatus.ACTIVE,
    legal_status: LegalUsageStatus = LegalUsageStatus.ALLOWED,
) -> SourceConfiguration:
    return SourceConfiguration(
        code,
        f"Source {code}",
        SourceType.PARTNER_API,
        status,
        legal_status,
        "adapter-1",
        "capabilities-1",
        frozenset(
            {
                SourceOperation.HEALTH,
                SourceOperation.CHECK_AVAILABILITY,
            }
        ),
        (f"https://{host}/api",),
        (f"cdn-{code}.example",),
        limits or SourceLimits(100, 60, 5, 300, 30),
    )


def policy(**changes: object) -> TransportPolicy:
    value = TransportPolicy(
        connect_timeout_seconds=2,
        read_timeout_seconds=3,
        total_timeout_seconds=20,
        max_attempts=3,
        base_backoff_seconds=1,
        max_backoff_seconds=8,
        jitter_ratio=0,
        max_retry_after_seconds=10,
        max_response_bytes=100,
        allowed_content_types=frozenset({"application/json"}),
        max_redirects=2,
        breaker_failure_threshold=2,
        breaker_window_seconds=60,
        breaker_recovery_seconds=30,
    )
    return replace(value, **changes)


def request(
    source_code: str = "source-a",
    *,
    host: str = "api-a.example",
    method: HttpMethod = HttpMethod.GET,
    url: str | None = None,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
) -> TransportRequest:
    return TransportRequest(
        source_code,
        SourceOperation.CHECK_AVAILABILITY,
        method,
        url or f"https://{host}/api/stock?token=must-not-leak",
        headers or {},
        body,
    )


async def test_safe_request_retries_upstream_with_deterministic_backoff() -> None:
    clock = FakeClock()
    wire = FakeWire(response(503), response())

    result = await SourceTransport(wire, clock, FixedRandom()).execute(
        source(),
        policy(),
        request(),
    )

    assert result.body == b"{}"
    assert result.attempts == 2
    assert clock.sleeps == [1.0]
    assert len(wire.calls) == 2
    assert wire.calls[0]["connect_timeout_seconds"] == 2
    assert wire.calls[0]["read_timeout_seconds"] == 3


async def test_retry_after_is_capped_and_does_not_create_storm() -> None:
    clock = FakeClock()
    wire = FakeWire(
        response(429, headers={"Retry-After": "999"}),
        response(429, headers={"Retry-After": "999"}),
        response(),
    )

    result = await SourceTransport(wire, clock, FixedRandom()).execute(
        source(),
        policy(total_timeout_seconds=40),
        request(),
    )

    assert result.attempts == 3
    assert clock.sleeps == [10.0, 10.0]


@pytest.mark.parametrize(
    ("result", "kind"),
    [
        (response(400), TransportFailureKind.CLIENT),
        (response(501), TransportFailureKind.UPSTREAM),
        (WireTimeoutError(), TransportFailureKind.TIMEOUT),
    ],
)
async def test_terminal_failures_are_classified_without_unsafe_retry(
    result: WireResponse | Exception,
    kind: TransportFailureKind,
) -> None:
    wire = FakeWire(result)

    with pytest.raises(TransportFailure) as captured:
        await SourceTransport(wire, FakeClock(), FixedRandom()).execute(
            source(),
            policy(),
            request(method=HttpMethod.POST),
        )

    assert captured.value.kind is kind
    assert len(wire.calls) == 1


async def test_deadline_prevents_retry_and_unsafe_post_is_never_retried() -> None:
    clock = FakeClock()
    post_wire = FakeWire(response(503), response())
    with pytest.raises(TransportFailure) as post_error:
        await SourceTransport(post_wire, clock, FixedRandom()).execute(
            source(),
            policy(),
            request(method=HttpMethod.POST, body=b"sensitive"),
        )
    assert post_error.value.kind is TransportFailureKind.UPSTREAM
    assert len(post_wire.calls) == 1

    deadline_wire = FakeWire(WireNetworkError(), response())
    with pytest.raises(TransportFailure) as deadline_error:
        await SourceTransport(deadline_wire, clock, FixedRandom()).execute(
            source(),
            policy(
                connect_timeout_seconds=0.5,
                read_timeout_seconds=0.5,
                total_timeout_seconds=1,
                base_backoff_seconds=1,
            ),
            request(),
        )
    assert deadline_error.value.kind is TransportFailureKind.NETWORK
    assert len(deadline_wire.calls) == 1


async def test_cancellation_propagates_without_classification() -> None:
    class CancelledWire:
        async def request(self, **values: object) -> WireResponse:
            del values
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await SourceTransport(CancelledWire(), FakeClock(), FixedRandom()).execute(
            source(),
            policy(),
            request(),
        )


async def test_redirect_is_allowlisted_and_cross_host_credentials_are_removed() -> None:
    diagnostics = Diagnostics()
    wire = FakeWire(
        response(302, headers={"Location": "https://cdn-source-a.example/data"}),
        response(values=(b'{"stock":1}',)),
    )

    result = await SourceTransport(
        wire,
        FakeClock(),
        FixedRandom(),
        diagnostics=diagnostics,
    ).execute(
        source(),
        policy(),
        request(
            headers={
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "Accept": "application/json",
            }
        ),
    )

    second_headers = wire.calls[1]["headers"]
    assert isinstance(second_headers, dict)
    assert second_headers == {"Accept": "application/json"}
    assert result.final_url == "https://cdn-source-a.example/data"
    serialized = repr(diagnostics.values)
    assert "must-not-leak" not in serialized
    assert "Bearer" not in serialized
    assert "session" not in serialized


async def test_untrusted_redirect_is_rejected_before_following() -> None:
    wire = FakeWire(response(302, headers={"Location": "https://evil.example/data"}))

    with pytest.raises(TransportFailure) as captured:
        await SourceTransport(wire, FakeClock(), FixedRandom()).execute(
            source(),
            policy(),
            request(),
        )

    assert captured.value.kind is TransportFailureKind.POLICY
    assert len(wire.calls) == 1


@pytest.mark.parametrize(
    "raw",
    [
        response(headers={"Content-Length": "101"}),
        response(values=(b"x" * 60, b"x" * 41)),
    ],
)
async def test_oversized_response_is_stopped_before_business_parse(
    raw: WireResponse,
) -> None:
    with pytest.raises(TransportFailure) as captured:
        await SourceTransport(
            FakeWire(raw),
            FakeClock(),
            FixedRandom(),
        ).execute(source(), policy(), request())

    assert captured.value.kind is TransportFailureKind.POLICY


async def test_wrong_content_type_is_schema_failure() -> None:
    with pytest.raises(TransportFailure) as captured:
        await SourceTransport(
            FakeWire(response(content_type="text/html")),
            FakeClock(),
            FixedRandom(),
        ).execute(source(), policy(), request())

    assert captured.value.kind is TransportFailureKind.SCHEMA


async def test_compressed_response_is_rejected_to_keep_byte_limit_exact() -> None:
    with pytest.raises(TransportFailure) as captured:
        await SourceTransport(
            FakeWire(response(headers={"Content-Encoding": "gzip"})),
            FakeClock(),
            FixedRandom(),
        ).execute(source(), policy(), request())

    assert captured.value.kind is TransportFailureKind.POLICY


async def test_rate_limit_and_circuit_are_isolated_by_source() -> None:
    clock = FakeClock()
    wire = FakeWire(WireNetworkError(), response(), response())
    transport = SourceTransport(wire, clock, FixedRandom())
    strict = SourceLimits(1, 100, 1, 300, 30)
    source_a = source(limits=strict)
    source_b = source("source-b", host="api-b.example", limits=strict)
    breaker_policy = policy(
        total_timeout_seconds=5,
        max_attempts=1,
        breaker_failure_threshold=1,
    )

    with pytest.raises(TransportFailure):
        await transport.execute(source_a, breaker_policy, request())
    with pytest.raises(TransportFailure) as open_error:
        await transport.execute(source_a, breaker_policy, request())
    successful = await transport.execute(
        source_b,
        breaker_policy,
        request("source-b", host="api-b.example"),
    )

    assert open_error.value.kind is TransportFailureKind.CIRCUIT_OPEN
    assert successful.status == 200


async def test_rate_bucket_of_one_source_does_not_delay_another() -> None:
    clock = FakeClock()
    wire = FakeWire(response(), response())
    transport = SourceTransport(wire, clock, FixedRandom())
    limits = SourceLimits(1, 100, 1, 300, 30)
    source_a = source(limits=limits)
    source_b = source("source-b", host="api-b.example", limits=limits)
    short_policy = policy(
        connect_timeout_seconds=0.5,
        read_timeout_seconds=0.5,
        total_timeout_seconds=5,
        max_attempts=1,
    )

    await transport.execute(source_a, short_policy, request())
    with pytest.raises(TransportFailure) as limited:
        await transport.execute(source_a, short_policy, request())
    other = await transport.execute(
        source_b,
        short_policy,
        request("source-b", host="api-b.example"),
    )

    assert limited.value.kind is TransportFailureKind.RATE_LIMITED
    assert other.status == 200


class BlockingWire:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def request(self, **values: object) -> WireResponse:
        del values
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        if self.active >= 2:
            self.started.set()
        await self.release.wait()
        self.active -= 1
        return response()


async def test_concurrency_is_bounded_per_source() -> None:
    wire = BlockingWire()
    value = source(limits=SourceLimits(100, 60, 2, 300, 30))
    transport = SourceTransport(wire, FakeClock(), FixedRandom())
    tasks = [asyncio.create_task(transport.execute(value, policy(), request())) for _ in range(3)]

    await asyncio.wait_for(wire.started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert wire.maximum == 2
    wire.release.set()
    await asyncio.gather(*tasks)


async def test_half_open_allows_only_one_probe_and_success_closes() -> None:
    clock = FakeClock()

    class ProbeWire:
        def __init__(self) -> None:
            self.calls = 0
            self.probe_started = asyncio.Event()
            self.release_probe = asyncio.Event()

        async def request(self, **values: object) -> WireResponse:
            del values
            self.calls += 1
            if self.calls == 1:
                raise WireNetworkError
            if self.calls == 2:
                self.probe_started.set()
                await self.release_probe.wait()
            return response()

    wire = ProbeWire()
    transport = SourceTransport(wire, clock, FixedRandom())
    breaker_policy = policy(max_attempts=1, breaker_failure_threshold=1)
    with pytest.raises(TransportFailure):
        await transport.execute(source(), breaker_policy, request())
    clock.value = 30
    probe = asyncio.create_task(transport.execute(source(), breaker_policy, request()))
    await asyncio.wait_for(wire.probe_started.wait(), timeout=1)
    with pytest.raises(TransportFailure) as second:
        await transport.execute(source(), breaker_policy, request())
    assert second.value.kind is TransportFailureKind.CIRCUIT_OPEN
    wire.release_probe.set()
    await probe

    recovered = await transport.execute(source(), breaker_policy, request())
    assert recovered.status == 200


async def test_disabled_or_legal_blocked_source_never_reaches_wire() -> None:
    for value in (
        source(status=SourceStatus.DISABLED),
        source(legal_status=LegalUsageStatus.BLOCKED),
    ):
        wire = FakeWire(response())
        with pytest.raises(TransportFailure) as captured:
            await SourceTransport(wire, FakeClock(), FixedRandom()).execute(
                value,
                policy(),
                request(),
            )
        assert captured.value.kind is TransportFailureKind.POLICY
        assert not wire.calls
