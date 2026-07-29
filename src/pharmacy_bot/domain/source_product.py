from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SourceProductStatus(StrEnum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SourceProductAttributes:
    kind: str
    active_ingredient: str | None = None
    manufacturer: str | None = None
    form: str | None = None
    dosage: str | None = None
    concentration: str | None = None
    package_count: int | None = None
    volume: str | None = None
    route: str | None = None
    package_variant: str | None = None


@dataclass(frozen=True, slots=True)
class SourceProductInput:
    source_code: str
    external_id: str
    canonical_url: str
    raw_name: str
    attributes: SourceProductAttributes
    status: SourceProductStatus
    semantic_fingerprint: str
    search_document: str


@dataclass(frozen=True, slots=True)
class SourceProduct:
    id: int
    source_code: str
    external_id: str
    canonical_url: str
    raw_name: str
    attributes: SourceProductAttributes
    status: SourceProductStatus
    semantic_fingerprint: str
    search_document: str
    canonical_product_id: int | None
    canonical_product_version: int | None
    version: int
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourceProductPage:
    items: tuple[SourceProduct, ...]
    next_after_id: int | None


class SourceProductConflict(Exception):
    pass
