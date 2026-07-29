from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SourceType(StrEnum):
    PARTNER_API = "partner_api"
    PUBLIC_API = "public_api"
    WEBHOOK = "webhook"
    EXPORT = "export"
    PUBLIC_PAGE = "public_page"
    MANUAL = "manual"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DEGRADED = "degraded"


class LegalUsageStatus(StrEnum):
    ALLOWED = "allowed"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class SourceOperation(StrEnum):
    HEALTH = "health"
    SEARCH_PRODUCTS = "search_products"
    GET_PRODUCT = "get_product"
    LIST_PHARMACIES = "list_pharmacies"
    CHECK_AVAILABILITY = "check_availability"
    GET_PRICE = "get_price"
    RECEIVE_WEBHOOK = "receive_webhook"
    IMPORT_EXPORT = "import_export"


@dataclass(frozen=True, slots=True)
class SourceLimits:
    requests_per_window: int
    window_seconds: int
    max_concurrency: int
    freshness_seconds: int
    cache_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class SourceConfiguration:
    code: str
    name: str
    source_type: SourceType
    status: SourceStatus
    legal_status: LegalUsageStatus
    adapter_version: str
    capability_version: str
    capabilities: frozenset[SourceOperation]
    base_urls: tuple[str, ...]
    redirect_hosts: tuple[str, ...]
    limits: SourceLimits


@dataclass(frozen=True, slots=True)
class Source:
    id: int
    version: int
    configuration: SourceConfiguration
    fingerprint: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourceOperationDecision:
    allowed: bool
    reasons: tuple[str, ...]


class SourceRegistryConflict(Exception):
    pass


class StaleSourceVersion(Exception):
    pass
