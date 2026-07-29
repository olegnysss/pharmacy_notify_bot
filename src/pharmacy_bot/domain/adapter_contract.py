from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pharmacy_bot.domain.source_registry import SourceOperation


class AdapterErrorKind(StrEnum):
    INVALID_REQUEST = "invalid_request"
    INVALID_RESULT = "invalid_result"
    UNSUPPORTED_VERSION = "unsupported_version"
    BAD_PROVENANCE = "bad_provenance"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AdapterCallContext:
    correlation_id: str
    causation_id: str | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class HealthQuery:
    pass


@dataclass(frozen=True, slots=True)
class ProductSearchQuery:
    text: str
    cursor: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class ProductCardQuery:
    external_id: str


@dataclass(frozen=True, slots=True)
class PharmacyListQuery:
    region_key: str | None
    cursor: str | None
    limit: int


@dataclass(frozen=True, slots=True)
class AvailabilityQuery:
    external_product_ids: tuple[str, ...]
    pharmacy_external_ids: tuple[str, ...]


AdapterQuery = (
    HealthQuery | ProductSearchQuery | ProductCardQuery | PharmacyListQuery | AvailabilityQuery
)


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    operation: SourceOperation
    context: AdapterCallContext
    query: AdapterQuery


@dataclass(frozen=True, slots=True)
class NormalizedProduct:
    external_id: str
    name: str
    canonical_url: str | None
    manufacturer: str | None
    form: str | None
    dosage: str | None
    package_count: int | None
    barcode: str | None


@dataclass(frozen=True, slots=True)
class NormalizedPharmacy:
    external_id: str
    name: str
    address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    region_key: str | None


@dataclass(frozen=True, slots=True)
class NormalizedAvailability:
    external_product_id: str
    pharmacy_external_id: str | None
    status: AvailabilityStatus
    quantity: int | None
    price: Decimal | None
    currency: str | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class HealthResult:
    healthy: bool
    observed_at: datetime
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ProductSearchResult:
    products: tuple[NormalizedProduct, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ProductCardResult:
    product: NormalizedProduct | None


@dataclass(frozen=True, slots=True)
class PharmacyListResult:
    pharmacies: tuple[NormalizedPharmacy, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    items: tuple[NormalizedAvailability, ...]


@dataclass(frozen=True, slots=True)
class UnsupportedOperationResult:
    operation: SourceOperation
    reason_code: str


AdapterResult = (
    HealthResult
    | ProductSearchResult
    | ProductCardResult
    | PharmacyListResult
    | AvailabilityResult
    | UnsupportedOperationResult
)


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    source_code: str
    adapter_version: str
    contract_versions: frozenset[str]
    operations: frozenset[SourceOperation]


@dataclass(frozen=True, slots=True)
class AdapterEnvelope:
    source_code: str
    adapter_version: str
    contract_version: str
    schema_version: str
    operation: SourceOperation
    result: AdapterResult


@dataclass(frozen=True, slots=True)
class AdapterIngestionReceipt:
    id: int
    source_id: int
    idempotency_key: str
    request_fingerprint: str
    result_fingerprint: str
    correlation_id: str
    causation_id: str | None
    envelope: AdapterEnvelope
    created_at: datetime


class PharmacyAdapter(Protocol):
    @property
    def descriptor(self) -> AdapterDescriptor: ...

    async def execute(self, request: AdapterRequest) -> AdapterEnvelope: ...


class AdapterContractError(Exception):
    def __init__(self, kind: AdapterErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class AdapterTemporaryError(Exception):
    pass


class AdapterPermanentError(Exception):
    pass
