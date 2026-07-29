from __future__ import annotations

import base64
import json
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Protocol

from pharmacy_bot.application.geography import GeographyPolicy, distance_meters
from pharmacy_bot.domain.geography import Coordinate
from pharmacy_bot.domain.pharmacy_directory import (
    Pharmacy,
    PharmacyDirectoryConflict,
    PharmacyIdentity,
    PharmacyMappingActor,
    PharmacyMatchLevel,
    PharmacyMatchResult,
    PharmacyPage,
    PharmacyStatus,
    SourcePharmacy,
)

_KEY = re.compile(r"^[\w.-]{1,128}$", re.UNICODE)


class PharmacyDirectoryRepository(Protocol):
    async def create_or_get_pharmacy(
        self,
        identity: PharmacyIdentity,
        status: PharmacyStatus,
        fingerprint: str,
        *,
        now: datetime,
    ) -> Pharmacy: ...

    async def upsert_source(
        self,
        source_code: str,
        external_id: str,
        identity: PharmacyIdentity,
        status: PharmacyStatus,
        fingerprint: str,
        *,
        now: datetime,
    ) -> SourcePharmacy: ...

    async def confirm_mapping(
        self,
        source_pharmacy_id: int,
        canonical_pharmacy_id: int,
        result: PharmacyMatchResult,
        actor: PharmacyMappingActor,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> SourcePharmacy: ...

    async def revoke_mapping(
        self,
        source_pharmacy_id: int,
        expected_mapping_version: int,
        actor: PharmacyMappingActor,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> SourcePharmacy: ...

    async def search_radius(
        self,
        center: Coordinate,
        radius_meters: int,
        *,
        after_distance: int | None,
        after_id: int | None,
        limit: int,
    ) -> PharmacyPage: ...


class PharmacyDirectoryService:
    def __init__(
        self,
        repository: PharmacyDirectoryRepository,
        geography: GeographyPolicy,
        *,
        max_page_size: int = 50,
    ) -> None:
        self._repository = repository
        self._geography = geography
        self._max_page_size = max_page_size

    def normalize_identity(self, raw: PharmacyIdentity) -> PharmacyIdentity:
        name = self._text(raw.name, 256)
        address = self._text(raw.normalized_address, 512)
        coordinate = None
        if raw.coordinate:
            coordinate = self._geography._coordinate(raw.coordinate)
        return PharmacyIdentity(
            name=name,
            normalized_address=self._normalize_address(address),
            network_key=self._key(raw.network_key),
            coordinate=coordinate,
            kind=raw.kind,
            trusted_identifier=self._key(raw.trusted_identifier),
        )

    async def create_or_get(
        self,
        raw: PharmacyIdentity,
        status: PharmacyStatus,
        *,
        now: datetime,
    ) -> Pharmacy:
        identity = self.normalize_identity(raw)
        return await self._repository.create_or_get_pharmacy(
            identity, status, self.fingerprint(identity, status), now=now
        )

    async def ingest_source(
        self,
        source_code: str,
        external_id: str,
        raw: PharmacyIdentity,
        status: PharmacyStatus,
        *,
        now: datetime,
    ) -> SourcePharmacy:
        source = self._key(source_code)
        external = self._key(external_id)
        if source is None or external is None:
            raise PharmacyDirectoryConflict("source key is invalid")
        identity = self.normalize_identity(raw)
        return await self._repository.upsert_source(
            source,
            external,
            identity,
            status,
            self.fingerprint(identity, status),
            now=now,
        )

    async def confirm_mapping(
        self,
        source_pharmacy_id: int,
        canonical_pharmacy_id: int,
        result: PharmacyMatchResult,
        actor: PharmacyMappingActor,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> SourcePharmacy:
        if "pharmacy_mapping" not in actor.roles:
            raise PharmacyDirectoryConflict("actor cannot confirm pharmacy mappings")
        if result.level is PharmacyMatchLevel.MISMATCH:
            raise PharmacyDirectoryConflict("mismatched pharmacies cannot be linked")
        if not 8 <= len(idempotency_key) <= 128:
            raise PharmacyDirectoryConflict("idempotency key is invalid")
        return await self._repository.confirm_mapping(
            source_pharmacy_id,
            canonical_pharmacy_id,
            result,
            actor,
            idempotency_key,
            now=now,
        )

    async def revoke_mapping(
        self,
        source_pharmacy_id: int,
        expected_mapping_version: int,
        actor: PharmacyMappingActor,
        idempotency_key: str,
        *,
        now: datetime,
    ) -> SourcePharmacy:
        if "pharmacy_mapping" not in actor.roles:
            raise PharmacyDirectoryConflict("actor cannot revoke pharmacy mappings")
        if not 8 <= len(idempotency_key) <= 128:
            raise PharmacyDirectoryConflict("idempotency key is invalid")
        return await self._repository.revoke_mapping(
            source_pharmacy_id,
            expected_mapping_version,
            actor,
            idempotency_key,
            now=now,
        )

    async def search_radius(
        self,
        center: Coordinate,
        radius_meters: int,
        *,
        cursor: str | None = None,
        page_size: int = 20,
    ) -> PharmacyPage:
        center = self._geography._coordinate(center)
        if not 1 <= page_size <= self._max_page_size or not 1 <= radius_meters <= 500_000:
            raise PharmacyDirectoryConflict("radius search limits are invalid")
        after_distance, after_id = self.decode_cursor(cursor) if cursor else (None, None)
        return await self._repository.search_radius(
            center,
            radius_meters,
            after_distance=after_distance,
            after_id=after_id,
            limit=page_size,
        )

    @staticmethod
    def match(source: PharmacyIdentity, canonical: PharmacyIdentity) -> PharmacyMatchResult:
        reasons: list[str] = []
        if source.kind is not canonical.kind:
            return PharmacyMatchResult(PharmacyMatchLevel.MISMATCH, 0, ("kind_mismatch",))
        if (
            source.network_key
            and canonical.network_key
            and source.network_key != canonical.network_key
        ):
            return PharmacyMatchResult(PharmacyMatchLevel.MISMATCH, 0, ("network_mismatch",))
        distance = None
        if source.coordinate and canonical.coordinate:
            distance = distance_meters(source.coordinate, canonical.coordinate)
            if distance > 250:
                return PharmacyMatchResult(PharmacyMatchLevel.MISMATCH, 0, ("coordinate_mismatch",))
            reasons.append(f"distance:{distance}")
        if source.trusted_identifier and source.trusted_identifier == canonical.trusted_identifier:
            return PharmacyMatchResult(
                PharmacyMatchLevel.EXACT, 100, ("trusted_identifier", *reasons)
            )
        address_score = round(
            SequenceMatcher(None, source.normalized_address, canonical.normalized_address).ratio()
            * 100
        )
        same_address = source.normalized_address == canonical.normalized_address
        if same_address and distance is not None and distance <= 30 and source.network_key:
            level, score = PharmacyMatchLevel.EXACT, 100
        elif same_address and (distance is None or distance <= 100):
            level, score = PharmacyMatchLevel.PROBABLE, 85
        elif address_score >= 80 and (distance is None or distance <= 150):
            level, score = PharmacyMatchLevel.CANDIDATE, address_score
        else:
            level, score = PharmacyMatchLevel.MISMATCH, 0
        return PharmacyMatchResult(level, score, (f"address_similarity:{address_score}", *reasons))

    @staticmethod
    def fingerprint(
        identity: PharmacyIdentity,
        status: PharmacyStatus = PharmacyStatus.ACTIVE,
    ) -> str:
        payload = asdict(identity)
        payload["kind"] = identity.kind.value
        payload["status"] = status.value
        if identity.coordinate:
            payload["coordinate"] = {
                "latitude": str(identity.coordinate.latitude),
                "longitude": str(identity.coordinate.longitude),
            }
        return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def encode_cursor(distance: int, pharmacy_id: int) -> str:
        return base64.urlsafe_b64encode(f"{distance}:{pharmacy_id}".encode()).decode().rstrip("=")

    @staticmethod
    def decode_cursor(value: str) -> tuple[int, int]:
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
            distance, pharmacy_id = (int(item) for item in raw.split(":"))
        except (ValueError, UnicodeError) as error:
            raise PharmacyDirectoryConflict("pagination cursor is invalid") from error
        if distance < 0 or pharmacy_id <= 0:
            raise PharmacyDirectoryConflict("pagination cursor is invalid")
        return distance, pharmacy_id

    @staticmethod
    def _normalize_address(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").split())

    @staticmethod
    def _text(value: str, limit: int) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized or len(normalized) > limit:
            raise PharmacyDirectoryConflict("pharmacy text field is invalid")
        return normalized

    @staticmethod
    def _key(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value).strip().casefold()
        if not _KEY.fullmatch(normalized):
            raise PharmacyDirectoryConflict("pharmacy key is invalid")
        return normalized
