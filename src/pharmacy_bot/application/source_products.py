from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from pharmacy_bot.application.catalog_normalization import (
    CatalogNormalizer,
    NormalizationError,
)
from pharmacy_bot.domain.source_product import (
    SourceProduct,
    SourceProductAttributes,
    SourceProductInput,
    SourceProductPage,
    SourceProductStatus,
)

_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_EXTERNAL_ID = re.compile(r"^[^\s\x00-\x1f]{1,256}$")
_UNSAFE_TEXT = re.compile(r"<\s*/?\s*[a-z]|javascript\s*:|data\s*:", re.IGNORECASE)
_FIELDS = frozenset(
    {
        "external_id",
        "canonical_url",
        "raw_name",
        "kind",
        "active_ingredient",
        "manufacturer",
        "form",
        "dosage",
        "concentration",
        "package_count",
        "volume",
        "route",
        "package_variant",
        "status",
    }
)


class SourcePayloadError(Exception):
    """A safe base error which never embeds source payload data."""


class SourcePayloadValidationError(SourcePayloadError):
    pass


class IncompleteSourcePayload(SourcePayloadError):
    pass


class UnknownSourceError(SourcePayloadError):
    pass


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    code: str
    allowed_hosts: tuple[str, ...]


class SourceProductRepository(Protocol):
    async def upsert(
        self,
        value: SourceProductInput,
        *,
        now: datetime,
    ) -> SourceProduct: ...

    async def search(
        self,
        normalized_query: str,
        *,
        after_id: int | None,
        limit: int,
    ) -> SourceProductPage: ...


class SourcePayloadParser:
    def __init__(
        self,
        normalizer: CatalogNormalizer,
        registrations: tuple[SourceRegistration, ...],
        *,
        max_payload_bytes: int = 32_768,
    ) -> None:
        self._normalizer = normalizer
        self._max_payload_bytes = max_payload_bytes
        self._sources: dict[str, frozenset[str]] = {}
        for registration in registrations:
            code = registration.code.strip().casefold()
            if not _CODE.fullmatch(code) or code in self._sources:
                raise ValueError("source registration code is invalid or duplicated")
            hosts = frozenset(host.strip(".").casefold() for host in registration.allowed_hosts)
            if not hosts or any(not host or "/" in host for host in hosts):
                raise ValueError("source registration hosts are invalid")
            self._sources[code] = hosts

    def parse(self, source_code: str, payload: Mapping[str, object]) -> SourceProductInput:
        code = source_code.strip().casefold()
        allowed_hosts = self._sources.get(code)
        if allowed_hosts is None:
            raise UnknownSourceError("source is not registered")
        self._validate_payload_envelope(payload)
        external_id = self._required_text(payload, "external_id", 256)
        if not _EXTERNAL_ID.fullmatch(external_id):
            raise SourcePayloadValidationError("external_id is invalid")
        canonical_url = self._canonical_url(
            self._required_text(payload, "canonical_url", 2048),
            allowed_hosts,
        )
        raw_name = self._required_text(payload, "raw_name", 512)
        kind = self._enum_value(payload, "kind", {"medicine", "other"}, default="medicine")
        status = SourceProductStatus(
            self._enum_value(
                payload,
                "status",
                {item.value for item in SourceProductStatus},
                default=SourceProductStatus.ACTIVE.value,
            )
        )
        package_count = self._optional_positive_int(payload, "package_count")
        raw_attributes = {
            key: self._optional_text(payload, key, limit)
            for key, limit in (
                ("active_ingredient", 512),
                ("manufacturer", 512),
                ("form", 128),
                ("dosage", 128),
                ("concentration", 128),
                ("volume", 128),
                ("route", 128),
                ("package_variant", 256),
            )
        }
        attributes = SourceProductAttributes(
            kind=kind,
            active_ingredient=self._normalize_text(raw_attributes["active_ingredient"]),
            manufacturer=self._normalize_text(raw_attributes["manufacturer"]),
            form=(
                self._normalizer.normalize_form(raw_attributes["form"])
                if raw_attributes["form"]
                else None
            ),
            dosage=self._normalized_quantity(raw_attributes["dosage"]),
            concentration=self._normalized_concentration(raw_attributes["concentration"]),
            package_count=package_count,
            volume=self._normalized_quantity(raw_attributes["volume"]),
            route=self._normalize_text(raw_attributes["route"]),
            package_variant=self._normalize_text(raw_attributes["package_variant"]),
        )
        semantic = {
            "canonical_url": canonical_url,
            "raw_name": self._normalizer.normalize_text(raw_name),
            "attributes": asdict(attributes),
            "status": status.value,
        }
        fingerprint = sha256(
            json.dumps(
                semantic,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return SourceProductInput(
            source_code=code,
            external_id=external_id,
            canonical_url=canonical_url,
            raw_name=raw_name,
            attributes=attributes,
            status=status,
            semantic_fingerprint=fingerprint,
            search_document=self._search_document(raw_name, attributes),
        )

    def _validate_payload_envelope(self, payload: Mapping[str, object]) -> None:
        try:
            encoded = json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError) as error:
            raise SourcePayloadValidationError("payload is not JSON-compatible") from error
        if len(encoded) > self._max_payload_bytes:
            raise SourcePayloadValidationError("payload exceeds configured size limit")
        if unknown := set(payload) - _FIELDS:
            raise SourcePayloadValidationError(
                f"payload contains unsupported fields ({len(unknown)})"
            )

    @staticmethod
    def _required_text(payload: Mapping[str, object], key: str, limit: int) -> str:
        value = SourcePayloadParser._optional_text(payload, key, limit)
        if value is None:
            raise IncompleteSourcePayload(f"required field {key} is missing")
        return value

    @staticmethod
    def _optional_text(
        payload: Mapping[str, object],
        key: str,
        limit: int,
    ) -> str | None:
        raw = payload.get(key)
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise SourcePayloadValidationError(f"field {key} must be text")
        value = unicodedata.normalize("NFKC", raw).strip()
        if not value:
            return None
        if len(value) > limit or any(ord(character) < 32 for character in value):
            raise SourcePayloadValidationError(f"field {key} is invalid")
        if _UNSAFE_TEXT.search(value):
            raise SourcePayloadValidationError(f"field {key} contains active content")
        return value

    @staticmethod
    def _enum_value(
        payload: Mapping[str, object],
        key: str,
        allowed: set[str],
        *,
        default: str,
    ) -> str:
        value = payload.get(key, default)
        if not isinstance(value, str) or value not in allowed:
            raise SourcePayloadValidationError(f"field {key} has unsupported value")
        return value

    @staticmethod
    def _optional_positive_int(payload: Mapping[str, object], key: str) -> int | None:
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 100_000:
            raise SourcePayloadValidationError(f"field {key} must be a positive integer")
        return value

    @staticmethod
    def _canonical_url(value: str, allowed_hosts: frozenset[str]) -> str:
        try:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").casefold()
            port_number = parsed.port
        except ValueError as error:
            raise SourcePayloadValidationError("canonical_url is not allowed") from error
        host_allowed = any(host == item or host.endswith(f".{item}") for item in allowed_hosts)
        if (
            parsed.scheme != "https"
            or not host_allowed
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise SourcePayloadValidationError("canonical_url is not allowed")
        port = f":{port_number}" if port_number and port_number != 443 else ""
        return urlunsplit(("https", f"{host}{port}", parsed.path or "/", parsed.query, ""))

    def _normalized_quantity(self, raw: str | None) -> str | None:
        if raw is None:
            return None
        try:
            value = self._normalizer.parse_quantity(raw)
        except NormalizationError as error:
            raise SourcePayloadValidationError("quantity has unsupported format") from error
        return f"{self._decimal_text(value.value)} {value.unit}"

    def _normalized_concentration(self, raw: str | None) -> str | None:
        if raw is None:
            return None
        try:
            value = self._normalizer.parse_concentration(raw)
        except NormalizationError as error:
            raise SourcePayloadValidationError("concentration has unsupported format") from error
        return (
            f"{self._decimal_text(value.numerator.value)} {value.numerator.unit}/"
            f"{self._decimal_text(value.denominator.value)} {value.denominator.unit}"
        )

    @staticmethod
    def _decimal_text(value: Decimal) -> str:
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered or "0"

    def _normalize_text(self, raw: str | None) -> str | None:
        return self._normalizer.normalize_text(raw) if raw else None

    def _search_document(
        self,
        raw_name: str,
        attributes: SourceProductAttributes,
    ) -> str:
        parts: list[str] = [self._normalizer.normalize_text(raw_name)]
        parts.extend(
            item
            for item in (
                attributes.active_ingredient,
                attributes.manufacturer,
                attributes.form,
                attributes.dosage,
                attributes.concentration,
                str(attributes.package_count) if attributes.package_count else None,
                attributes.volume,
                attributes.route,
                attributes.package_variant,
            )
            if item
        )
        return " | ".join(parts)


class SourceProductService:
    def __init__(
        self,
        repository: SourceProductRepository,
        parser: SourcePayloadParser,
        normalizer: CatalogNormalizer,
        *,
        max_page_size: int = 50,
    ) -> None:
        self._repository = repository
        self._parser = parser
        self._normalizer = normalizer
        self._max_page_size = max_page_size

    async def ingest(
        self,
        source_code: str,
        payload: Mapping[str, object],
        *,
        now: datetime,
    ) -> SourceProduct:
        value = self._parser.parse(source_code, payload)
        return await self._repository.upsert(value, now=now)

    async def search(
        self,
        query: str,
        *,
        after_id: int | None = None,
        page_size: int = 20,
    ) -> SourceProductPage:
        if not 1 <= page_size <= self._max_page_size:
            raise SourcePayloadValidationError("page size is outside allowed range")
        if after_id is not None and after_id < 0:
            raise SourcePayloadValidationError("pagination key is invalid")
        normalized = self._normalizer.normalize_text(query.strip())
        if not 2 <= len(normalized) <= 256:
            raise SourcePayloadValidationError("search query length is invalid")
        return await self._repository.search(
            normalized,
            after_id=after_id,
            limit=page_size,
        )
