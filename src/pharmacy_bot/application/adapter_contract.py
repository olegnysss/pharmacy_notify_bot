from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import NoReturn, Protocol, cast
from uuid import UUID

from pharmacy_bot.application.source_registry import SourceRegistryService
from pharmacy_bot.domain.adapter_contract import (
    AdapterCallContext,
    AdapterContractError,
    AdapterDescriptor,
    AdapterEnvelope,
    AdapterErrorKind,
    AdapterIngestionReceipt,
    AdapterRequest,
    AdapterResult,
    AvailabilityQuery,
    AvailabilityResult,
    AvailabilityStatus,
    HealthQuery,
    HealthResult,
    NormalizedAvailability,
    NormalizedPharmacy,
    NormalizedProduct,
    PharmacyAdapter,
    PharmacyListQuery,
    PharmacyListResult,
    ProductCardQuery,
    ProductCardResult,
    ProductSearchQuery,
    ProductSearchResult,
    UnsupportedOperationResult,
)
from pharmacy_bot.domain.source_registry import Source, SourceOperation

CONTRACT_VERSION = "pharmacy-adapter/1.0"
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_OPERATION_QUERY_TYPES = {
    SourceOperation.HEALTH: HealthQuery,
    SourceOperation.SEARCH_PRODUCTS: ProductSearchQuery,
    SourceOperation.GET_PRODUCT: ProductCardQuery,
    SourceOperation.LIST_PHARMACIES: PharmacyListQuery,
    SourceOperation.CHECK_AVAILABILITY: AvailabilityQuery,
}
_RESULT_SCHEMAS: dict[type[object], tuple[SourceOperation | None, str]] = {
    HealthResult: (SourceOperation.HEALTH, "health/1"),
    ProductSearchResult: (SourceOperation.SEARCH_PRODUCTS, "product-search/1"),
    ProductCardResult: (SourceOperation.GET_PRODUCT, "product-card/1"),
    PharmacyListResult: (SourceOperation.LIST_PHARMACIES, "pharmacies/1"),
    AvailabilityResult: (SourceOperation.CHECK_AVAILABILITY, "availability/1"),
    UnsupportedOperationResult: (None, "unsupported/1"),
}


class AdapterIngestionRepository(Protocol):
    async def get(
        self,
        source_id: int,
        idempotency_key: str,
    ) -> AdapterIngestionReceipt | None: ...

    async def store(
        self,
        source_id: int,
        request_fingerprint: str,
        result_fingerprint: str,
        request: AdapterRequest,
        envelope: AdapterEnvelope,
        *,
        now: datetime,
    ) -> AdapterIngestionReceipt: ...


class AdapterContractValidator:
    def negotiate(self, descriptor: AdapterDescriptor) -> str:
        if CONTRACT_VERSION not in descriptor.contract_versions:
            raise AdapterContractError(
                AdapterErrorKind.UNSUPPORTED_VERSION,
                "adapter has no compatible contract version",
            )
        return CONTRACT_VERSION

    def validate_descriptor(self, source: Source, descriptor: AdapterDescriptor) -> None:
        configuration = source.configuration
        if (
            type(descriptor.contract_versions) is not frozenset
            or type(descriptor.operations) is not frozenset
            or descriptor.source_code != configuration.code
            or descriptor.adapter_version != configuration.adapter_version
        ):
            raise AdapterContractError(
                AdapterErrorKind.BAD_PROVENANCE,
                "adapter descriptor does not match registered source",
            )
        if not descriptor.operations <= configuration.capabilities:
            raise AdapterContractError(
                AdapterErrorKind.BAD_PROVENANCE,
                "adapter declares an unregistered capability",
            )
        if any(operation not in _OPERATION_QUERY_TYPES for operation in descriptor.operations):
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                "adapter declares an unsupported contract operation",
            )
        self.negotiate(descriptor)

    def validate_request(self, source: Source, request: AdapterRequest) -> None:
        expected = _OPERATION_QUERY_TYPES.get(request.operation)
        if expected is None or type(request.query) is not expected:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                "adapter request operation and query do not match",
            )
        decision = SourceRegistryService.operation_decision(
            source.configuration,
            request.operation,
        )
        if not decision.allowed:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                "adapter operation is not allowed for source",
            )
        self._context(request.context)
        query = request.query
        if isinstance(query, ProductSearchQuery):
            self._text(query.text, "search text", 256, request=True)
            self._optional_text(query.cursor, "search cursor", 512, request=True)
            self._limit(query.limit)
        elif isinstance(query, ProductCardQuery):
            self._text(query.external_id, "external product id", 256, request=True)
        elif isinstance(query, PharmacyListQuery):
            self._optional_text(query.region_key, "region key", 128, request=True)
            self._optional_text(query.cursor, "pharmacy cursor", 512, request=True)
            self._limit(query.limit)
        elif isinstance(query, AvailabilityQuery):
            self._identifier_list(
                query.external_product_ids,
                "external product ids",
                required=True,
            )
            self._identifier_list(
                query.pharmacy_external_ids,
                "pharmacy external ids",
                required=False,
            )

    def validate_envelope(
        self,
        source: Source,
        descriptor: AdapterDescriptor,
        request: AdapterRequest,
        envelope: AdapterEnvelope,
    ) -> None:
        if (
            envelope.source_code != source.configuration.code
            or envelope.source_code != descriptor.source_code
            or envelope.adapter_version != descriptor.adapter_version
        ):
            raise AdapterContractError(
                AdapterErrorKind.BAD_PROVENANCE,
                "adapter result provenance does not match request",
            )
        if envelope.contract_version != self.negotiate(descriptor):
            raise AdapterContractError(
                AdapterErrorKind.UNSUPPORTED_VERSION,
                "adapter returned an unsupported contract version",
            )
        if envelope.operation is not request.operation:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_RESULT,
                "adapter result operation does not match request",
            )
        result_type = type(envelope.result)
        schema = _RESULT_SCHEMAS.get(result_type)
        if schema is None:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_RESULT,
                "adapter returned an unknown result type",
            )
        expected_operation, expected_schema = schema
        if envelope.schema_version != expected_schema:
            raise AdapterContractError(
                AdapterErrorKind.UNSUPPORTED_VERSION,
                "adapter result schema version is unsupported",
            )
        if isinstance(envelope.result, UnsupportedOperationResult):
            if (
                envelope.result.operation is not request.operation
                or request.operation in descriptor.operations
            ):
                raise AdapterContractError(
                    AdapterErrorKind.INVALID_RESULT,
                    "adapter returned an invalid unsupported-operation result",
                )
            if (
                type(envelope.result.reason_code) is not str
                or _REASON_CODE.fullmatch(envelope.result.reason_code) is None
            ):
                raise AdapterContractError(
                    AdapterErrorKind.INVALID_RESULT,
                    "unsupported-operation reason is invalid",
                )
            return
        if expected_operation is not request.operation:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_RESULT,
                "adapter payload type does not match operation",
            )
        if request.operation not in descriptor.operations:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_RESULT,
                "adapter returned data for an undeclared operation",
            )
        self._result(source, request, envelope.result)

    def _result(
        self,
        source: Source,
        request: AdapterRequest,
        result: AdapterResult,
    ) -> None:
        if isinstance(result, HealthResult):
            if type(result.healthy) is not bool:
                self._invalid("health status is invalid")
            self._instant(result.observed_at)
            self._optional_text(result.message, "health message", 512)
        elif isinstance(result, ProductSearchResult):
            self._bounded_items(result.products, "products")
            self._optional_text(result.next_cursor, "product cursor", 512)
            self._products(source, result.products)
        elif isinstance(result, ProductCardResult):
            if result.product is not None:
                self._product(source, result.product)
                query = cast(ProductCardQuery, request.query)
                if result.product.external_id != query.external_id:
                    raise AdapterContractError(
                        AdapterErrorKind.INVALID_RESULT,
                        "product card external id does not match request",
                    )
        elif isinstance(result, PharmacyListResult):
            self._bounded_items(result.pharmacies, "pharmacies")
            self._optional_text(result.next_cursor, "pharmacy cursor", 512)
            self._pharmacies(result.pharmacies)
        elif isinstance(result, AvailabilityResult):
            self._bounded_items(result.items, "availability items")
            self._availability(cast(AvailabilityQuery, request.query), result.items)
        else:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_RESULT,
                "adapter result type is invalid",
            )

    def _products(
        self,
        source: Source,
        products: tuple[NormalizedProduct, ...],
    ) -> None:
        identifiers: set[str] = set()
        for product in products:
            self._product(source, product)
            if product.external_id in identifiers:
                raise AdapterContractError(
                    AdapterErrorKind.INVALID_RESULT,
                    "product result contains duplicate external ids",
                )
            identifiers.add(product.external_id)

    def _product(self, source: Source, product: NormalizedProduct) -> None:
        if type(product) is not NormalizedProduct:
            self._invalid("product item type is invalid")
        self._text(product.external_id, "external product id", 256)
        self._text(product.name, "product name", 512)
        for label, value, limit in (
            ("manufacturer", product.manufacturer, 256),
            ("form", product.form, 128),
            ("dosage", product.dosage, 128),
            ("barcode", product.barcode, 64),
        ):
            self._optional_text(value, label, limit)
        if product.package_count is not None and (
            type(product.package_count) is not int or not 1 <= product.package_count <= 1_000_000
        ):
            self._invalid("product package count is invalid")
        if product.canonical_url is not None:
            self._optional_text(product.canonical_url, "product URL", 2048)
            if not SourceRegistryService.host_allowed(source.configuration, product.canonical_url):
                self._invalid("product URL is invalid")

    def _pharmacies(self, values: tuple[NormalizedPharmacy, ...]) -> None:
        identifiers: set[str] = set()
        for value in values:
            if type(value) is not NormalizedPharmacy:
                self._invalid("pharmacy item type is invalid")
            self._text(value.external_id, "external pharmacy id", 256)
            self._text(value.name, "pharmacy name", 256)
            self._optional_text(value.address, "pharmacy address", 512)
            self._optional_text(value.region_key, "pharmacy region", 128)
            if (value.latitude is None) is not (value.longitude is None):
                self._invalid("pharmacy coordinates must be complete or missing")
            longitude = value.longitude
            if value.latitude is not None and (
                type(value.latitude) is not Decimal
                or longitude is None
                or type(longitude) is not Decimal
                or not value.latitude.is_finite()
                or not longitude.is_finite()
                or not Decimal("-90") <= value.latitude <= Decimal("90")
                or not Decimal("-180") <= longitude <= Decimal("180")
            ):
                self._invalid("pharmacy coordinates are invalid")
            if value.external_id in identifiers:
                self._invalid("pharmacy result contains duplicate external ids")
            identifiers.add(value.external_id)

    def _availability(
        self,
        query: AvailabilityQuery,
        values: tuple[NormalizedAvailability, ...],
    ) -> None:
        requested_products = set(query.external_product_ids)
        requested_pharmacies = set(query.pharmacy_external_ids)
        identities: set[tuple[str, str | None]] = set()
        for value in values:
            if type(value) is not NormalizedAvailability:
                self._invalid("availability item type is invalid")
            self._text(value.external_product_id, "availability product id", 256)
            self._optional_text(
                value.pharmacy_external_id,
                "availability pharmacy id",
                256,
            )
            if value.external_product_id not in requested_products:
                self._invalid("availability contains an unrequested product")
            if requested_pharmacies and value.pharmacy_external_id not in requested_pharmacies:
                self._invalid("availability contains an unrequested pharmacy")
            if type(value.status) is not AvailabilityStatus:
                self._invalid("availability status is invalid")
            if value.quantity is not None and (
                type(value.quantity) is not int or not 0 <= value.quantity <= 1_000_000_000
            ):
                self._invalid("availability quantity is invalid")
            if value.status is AvailabilityStatus.UNAVAILABLE and value.quantity not in {None, 0}:
                self._invalid("unavailable item cannot have positive quantity")
            if (value.price is None) is not (value.currency is None):
                self._invalid("availability price and currency must be paired")
            if value.price is not None and (
                type(value.price) is not Decimal
                or not value.price.is_finite()
                or value.price < 0
                or value.price > Decimal("1000000000")
                or value.currency is None
                or re.fullmatch(r"[A-Z]{3}", value.currency) is None
            ):
                self._invalid("availability price is invalid")
            self._instant(value.observed_at)
            identity = (value.external_product_id, value.pharmacy_external_id)
            if identity in identities:
                self._invalid("availability result contains duplicate identities")
            identities.add(identity)

    @staticmethod
    def _bounded_items(values: tuple[object, ...], label: str) -> None:
        if type(values) is not tuple or len(values) > 100:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_RESULT,
                f"{label} exceeds contract limit",
            )

    @staticmethod
    def _context(value: AdapterCallContext) -> None:
        if type(value) is not AdapterCallContext:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                "adapter correlation context is invalid",
            )
        try:
            correlation = UUID(value.correlation_id)
            if value.causation_id is not None:
                causation = UUID(value.causation_id)
                if str(causation) != value.causation_id:
                    raise ValueError
            if str(correlation) != value.correlation_id:
                raise ValueError
        except (AttributeError, TypeError, ValueError) as error:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                "adapter correlation context is invalid",
            ) from error
        if (
            type(value.idempotency_key) is not str
            or _IDEMPOTENCY_KEY.fullmatch(value.idempotency_key) is None
        ):
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                "adapter idempotency key is invalid",
            )

    @staticmethod
    def _limit(value: int) -> None:
        if type(value) is not int or not 1 <= value <= 100:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                "adapter page limit is invalid",
            )

    @classmethod
    def _identifier_list(
        cls,
        values: tuple[str, ...],
        label: str,
        *,
        required: bool,
    ) -> None:
        if (
            type(values) is not tuple
            or (required and not values)
            or len(values) > 100
            or len(set(values)) != len(values)
        ):
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                f"{label} is invalid",
            )
        for value in values:
            cls._text(value, label, 256, request=True)

    @staticmethod
    def _instant(value: datetime) -> None:
        if type(value) is not datetime:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_RESULT,
                "adapter timestamp must be UTC-aware",
            )
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_RESULT,
                "adapter timestamp must be UTC-aware",
            )

    @staticmethod
    def _text(
        value: str,
        label: str,
        limit: int,
        *,
        request: bool = False,
    ) -> None:
        if (
            type(value) is not str
            or not value
            or value != value.strip()
            or len(value) > limit
            or "\x00" in value
        ):
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST if request else AdapterErrorKind.INVALID_RESULT,
                f"{label} is invalid",
            )

    @classmethod
    def _optional_text(
        cls,
        value: str | None,
        label: str,
        limit: int,
        *,
        request: bool = False,
    ) -> None:
        if value is not None:
            cls._text(value, label, limit, request=request)

    @staticmethod
    def _invalid(message: str) -> None:
        raise AdapterContractError(AdapterErrorKind.INVALID_RESULT, message)


class AdapterIngestionService:
    def __init__(
        self,
        repository: AdapterIngestionRepository,
        validator: AdapterContractValidator | None = None,
    ) -> None:
        self._repository = repository
        self._validator = validator or AdapterContractValidator()

    async def execute(
        self,
        source: Source,
        adapter: PharmacyAdapter,
        request: AdapterRequest,
        *,
        now: datetime,
    ) -> AdapterIngestionReceipt:
        offset = now.utcoffset() if type(now) is datetime else None
        if offset is None or offset.total_seconds() != 0:
            raise AdapterContractError(
                AdapterErrorKind.INVALID_REQUEST,
                "adapter ingestion timestamp must be UTC-aware",
            )
        descriptor = adapter.descriptor
        self._validator.validate_descriptor(source, descriptor)
        self._validator.validate_request(source, request)
        request_fingerprint = adapter_request_fingerprint(request)
        existing = await self._repository.get(
            source.id,
            request.context.idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise AdapterContractError(
                    AdapterErrorKind.IDEMPOTENCY_CONFLICT,
                    "adapter idempotency key was reused for another request",
                )
            self._validator.validate_envelope(
                source,
                descriptor,
                request,
                existing.envelope,
            )
            return existing
        envelope = await adapter.execute(request)
        self._validator.validate_envelope(source, descriptor, request, envelope)
        return await self._repository.store(
            source.id,
            request_fingerprint,
            contract_fingerprint(envelope),
            request,
            envelope,
            now=now,
        )


def contract_fingerprint(value: object) -> str:
    return _digest(_canonical(value))


def adapter_request_fingerprint(value: AdapterRequest) -> str:
    return _digest(
        {
            "operation": value.operation.value,
            "query": _canonical(value.query),
        }
    )


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _canonical(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "_type": type(value).__name__,
            **{field.name: _canonical(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple | list):
        return [_canonical(item) for item in value]
    if isinstance(value, frozenset | set):
        return sorted((_canonical(item) for item in value), key=str)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported contract fingerprint value: {type(value).__name__}")


def encode_envelope(value: AdapterEnvelope) -> dict[str, object]:
    result = value.result
    if isinstance(result, HealthResult):
        result_type = "health"
        payload: dict[str, object] = {
            "healthy": result.healthy,
            "observed_at": result.observed_at.isoformat(),
            "message": result.message,
        }
    elif isinstance(result, ProductSearchResult):
        result_type = "product_search"
        payload = {
            "products": [_encode_product(item) for item in result.products],
            "next_cursor": result.next_cursor,
        }
    elif isinstance(result, ProductCardResult):
        result_type = "product_card"
        payload = {
            "product": (_encode_product(result.product) if result.product is not None else None)
        }
    elif isinstance(result, PharmacyListResult):
        result_type = "pharmacy_list"
        payload = {
            "pharmacies": [_encode_pharmacy(item) for item in result.pharmacies],
            "next_cursor": result.next_cursor,
        }
    elif isinstance(result, AvailabilityResult):
        result_type = "availability"
        payload = {"items": [_encode_availability(item) for item in result.items]}
    elif isinstance(result, UnsupportedOperationResult):
        result_type = "unsupported"
        payload = {
            "operation": result.operation.value,
            "reason_code": result.reason_code,
        }
    else:
        raise AdapterContractError(
            AdapterErrorKind.INVALID_RESULT,
            "cannot encode unknown adapter result",
        )
    return {
        "source_code": value.source_code,
        "adapter_version": value.adapter_version,
        "contract_version": value.contract_version,
        "schema_version": value.schema_version,
        "operation": value.operation.value,
        "result_type": result_type,
        "payload": payload,
    }


def decode_envelope(raw: object) -> AdapterEnvelope:
    value = _object(
        raw,
        {
            "source_code",
            "adapter_version",
            "contract_version",
            "schema_version",
            "operation",
            "result_type",
            "payload",
        },
    )
    source_code = _string(value["source_code"])
    adapter_version = _string(value["adapter_version"])
    contract_version = _string(value["contract_version"])
    schema_version = _string(value["schema_version"])
    operation = _operation(value["operation"])
    result_type = _string(value["result_type"])
    payload = value["payload"]
    if result_type == "health":
        item = _object(payload, {"healthy", "observed_at", "message"})
        healthy = item["healthy"]
        if not isinstance(healthy, bool):
            _corrupt()
        result: AdapterResult = HealthResult(
            healthy,
            _datetime(item["observed_at"]),
            _optional_string(item["message"]),
        )
    elif result_type == "product_search":
        item = _object(payload, {"products", "next_cursor"})
        result = ProductSearchResult(
            tuple(_decode_product(product) for product in _list(item["products"])),
            _optional_string(item["next_cursor"]),
        )
    elif result_type == "product_card":
        item = _object(payload, {"product"})
        product = item["product"]
        result = ProductCardResult(_decode_product(product) if product is not None else None)
    elif result_type == "pharmacy_list":
        item = _object(payload, {"pharmacies", "next_cursor"})
        result = PharmacyListResult(
            tuple(_decode_pharmacy(pharmacy) for pharmacy in _list(item["pharmacies"])),
            _optional_string(item["next_cursor"]),
        )
    elif result_type == "availability":
        item = _object(payload, {"items"})
        result = AvailabilityResult(
            tuple(_decode_availability(entry) for entry in _list(item["items"]))
        )
    elif result_type == "unsupported":
        item = _object(payload, {"operation", "reason_code"})
        result = UnsupportedOperationResult(
            _operation(item["operation"]),
            _string(item["reason_code"]),
        )
    else:
        _corrupt()
    return AdapterEnvelope(
        source_code,
        adapter_version,
        contract_version,
        schema_version,
        operation,
        result,
    )


def _encode_product(value: NormalizedProduct) -> dict[str, object]:
    return {
        "external_id": value.external_id,
        "name": value.name,
        "canonical_url": value.canonical_url,
        "manufacturer": value.manufacturer,
        "form": value.form,
        "dosage": value.dosage,
        "package_count": value.package_count,
        "barcode": value.barcode,
    }


def _decode_product(raw: object) -> NormalizedProduct:
    value = _object(
        raw,
        {
            "external_id",
            "name",
            "canonical_url",
            "manufacturer",
            "form",
            "dosage",
            "package_count",
            "barcode",
        },
    )
    package_count = value["package_count"]
    if package_count is not None and (
        not isinstance(package_count, int) or isinstance(package_count, bool)
    ):
        _corrupt()
    return NormalizedProduct(
        _string(value["external_id"]),
        _string(value["name"]),
        _optional_string(value["canonical_url"]),
        _optional_string(value["manufacturer"]),
        _optional_string(value["form"]),
        _optional_string(value["dosage"]),
        package_count,
        _optional_string(value["barcode"]),
    )


def _encode_pharmacy(value: NormalizedPharmacy) -> dict[str, object]:
    return {
        "external_id": value.external_id,
        "name": value.name,
        "address": value.address,
        "latitude": str(value.latitude) if value.latitude is not None else None,
        "longitude": str(value.longitude) if value.longitude is not None else None,
        "region_key": value.region_key,
    }


def _decode_pharmacy(raw: object) -> NormalizedPharmacy:
    value = _object(
        raw,
        {
            "external_id",
            "name",
            "address",
            "latitude",
            "longitude",
            "region_key",
        },
    )
    return NormalizedPharmacy(
        _string(value["external_id"]),
        _string(value["name"]),
        _optional_string(value["address"]),
        _optional_decimal(value["latitude"]),
        _optional_decimal(value["longitude"]),
        _optional_string(value["region_key"]),
    )


def _encode_availability(value: NormalizedAvailability) -> dict[str, object]:
    return {
        "external_product_id": value.external_product_id,
        "pharmacy_external_id": value.pharmacy_external_id,
        "status": value.status.value,
        "quantity": value.quantity,
        "price": str(value.price) if value.price is not None else None,
        "currency": value.currency,
        "observed_at": value.observed_at.isoformat(),
    }


def _decode_availability(raw: object) -> NormalizedAvailability:
    value = _object(
        raw,
        {
            "external_product_id",
            "pharmacy_external_id",
            "status",
            "quantity",
            "price",
            "currency",
            "observed_at",
        },
    )
    quantity = value["quantity"]
    if quantity is not None and (not isinstance(quantity, int) or isinstance(quantity, bool)):
        _corrupt()
    try:
        status = AvailabilityStatus(_string(value["status"]))
    except ValueError:
        _corrupt()
    return NormalizedAvailability(
        _string(value["external_product_id"]),
        _optional_string(value["pharmacy_external_id"]),
        status,
        quantity,
        _optional_decimal(value["price"]),
        _optional_string(value["currency"]),
        _datetime(value["observed_at"]),
    )


def _object(raw: object, keys: set[str]) -> dict[str, object]:
    if (
        not isinstance(raw, dict)
        or set(raw) != keys
        or any(not isinstance(key, str) for key in raw)
    ):
        _corrupt()
    return cast(dict[str, object], raw)


def _list(raw: object) -> list[object]:
    if not isinstance(raw, list):
        _corrupt()
    return cast(list[object], raw)


def _string(raw: object) -> str:
    if not isinstance(raw, str):
        _corrupt()
    return raw


def _optional_string(raw: object) -> str | None:
    if raw is None:
        return None
    return _string(raw)


def _operation(raw: object) -> SourceOperation:
    try:
        return SourceOperation(_string(raw))
    except ValueError:
        _corrupt()


def _datetime(raw: object) -> datetime:
    try:
        return datetime.fromisoformat(_string(raw))
    except ValueError:
        _corrupt()


def _optional_decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(_string(raw))
    except Exception as error:
        raise AdapterContractError(
            AdapterErrorKind.INVALID_RESULT,
            "stored adapter result is corrupted",
        ) from error


def _corrupt() -> NoReturn:
    raise AdapterContractError(
        AdapterErrorKind.INVALID_RESULT,
        "stored adapter result is corrupted",
    )
