from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ProductInputMode(StrEnum):
    SEARCH = "search"
    LINK = "link"


class ProductDraftStatus(StrEnum):
    CHOOSE_METHOD = "choose_method"
    AWAITING_INPUT = "awaiting_input"
    SEARCHING = "searching"
    RESULTS = "results"
    NO_RESULTS = "no_results"
    CONFIRMATION = "confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    ERROR = "error"


class MatchConfidence(StrEnum):
    EXACT = "exact"
    PROBABLE = "probable"
    CANDIDATE = "candidate"


class DiscoveryStatus(StrEnum):
    SUCCESS = "success"
    EMPTY = "empty"
    TEMPORARY_ERROR = "temporary_error"


@dataclass(frozen=True, slots=True)
class ProductCandidate:
    candidate_key: str
    version: str
    name: str
    form: str | None = None
    dosage: str | None = None
    package: str | None = None
    manufacturer: str | None = None
    source_name: str | None = None
    source_host: str | None = None
    confidence: MatchConfidence = MatchConfidence.CANDIDATE
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryResponse:
    status: DiscoveryStatus
    candidates: tuple[ProductCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductDraft:
    id: int
    user_id: int
    generation: int
    status: ProductDraftStatus
    input_mode: ProductInputMode | None
    query_text: str | None
    source_host: str | None
    candidates: tuple[ProductCandidate, ...]
    selected_ordinal: int | None
    selected_candidate_version: str | None
    expires_at: datetime

    @property
    def selected_candidate(self) -> ProductCandidate | None:
        if self.selected_ordinal is None:
            return None
        return next(
            (
                candidate
                for candidate in self.candidates
                if candidate.ordinal == self.selected_ordinal
            ),
            None,
        )
