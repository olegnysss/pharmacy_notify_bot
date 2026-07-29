from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

import aiohttp

from pharmacy_bot.domain.source_transport import (
    HttpMethod,
    WireNetworkError,
    WireResponse,
    WireTimeoutError,
)


class _AiohttpWireBody:
    def __init__(self, response: aiohttp.ClientResponse) -> None:
        self._response = response

    async def _chunks(self) -> AsyncIterator[bytes]:
        async for chunk in self._response.content.iter_chunked(64 * 1024):
            yield bytes(chunk)

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._chunks()

    async def aclose(self) -> None:
        self._response.release()
        await self._response.wait_for_close()


class AiohttpWireTransport:
    """TLS-verifying, redirect-free and cookie-free streaming HTTP transport."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        if not isinstance(session.cookie_jar, aiohttp.DummyCookieJar):
            raise ValueError("source transport session must use DummyCookieJar")
        self._session = session

    @classmethod
    def create(cls) -> AiohttpWireTransport:
        session = aiohttp.ClientSession(
            cookie_jar=aiohttp.DummyCookieJar(),
            connector=aiohttp.TCPConnector(limit=0, ssl=True),
            trust_env=False,
            auto_decompress=False,
        )
        return cls(session)

    async def close(self) -> None:
        await self._session.close()

    async def __aenter__(self) -> AiohttpWireTransport:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback
        await self.close()

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
    ) -> WireResponse:
        safe_headers = {
            key: value for key, value in headers.items() if key.casefold() != "accept-encoding"
        }
        safe_headers["Accept-Encoding"] = "identity"
        timeout = aiohttp.ClientTimeout(
            total=total_timeout_seconds,
            connect=connect_timeout_seconds,
            sock_connect=connect_timeout_seconds,
            sock_read=read_timeout_seconds,
        )
        try:
            response = await self._session.request(
                method.value,
                url,
                headers=safe_headers,
                data=body,
                allow_redirects=False,
                timeout=timeout,
                ssl=True,
            )
        except TimeoutError as error:
            raise WireTimeoutError from error
        except aiohttp.ClientError as error:
            raise WireNetworkError from error
        return WireResponse(
            response.status,
            dict(response.headers),
            response.headers.get("Content-Type"),
            _AiohttpWireBody(response),
        )
