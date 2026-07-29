from __future__ import annotations

import asyncio
from collections import OrderedDict

from pharmacy_bot.domain.source_integration import SourceCacheRecord


class InMemorySourceCacheRepository:
    def __init__(self, *, max_entries: int = 10_000) -> None:
        if not 1 <= max_entries <= 1_000_000:
            raise ValueError("cache entry bound is invalid")
        self._max_entries = max_entries
        self._records: OrderedDict[str, SourceCacheRecord] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, namespace_fingerprint: str) -> SourceCacheRecord | None:
        async with self._lock:
            value = self._records.get(namespace_fingerprint)
            if value is not None:
                self._records.move_to_end(namespace_fingerprint)
            return value

    async def put(
        self,
        namespace_fingerprint: str,
        record: SourceCacheRecord,
    ) -> None:
        async with self._lock:
            self._records[namespace_fingerprint] = record
            self._records.move_to_end(namespace_fingerprint)
            while len(self._records) > self._max_entries:
                self._records.popitem(last=False)

    async def delete(self, namespace_fingerprint: str) -> None:
        async with self._lock:
            self._records.pop(namespace_fingerprint, None)

    async def invalidate_adapter(
        self,
        source_code: str,
        active_adapter_version: str,
    ) -> int:
        async with self._lock:
            keys = [
                key
                for key, record in self._records.items()
                if record.source_code == source_code
                and record.adapter_version != active_adapter_version
            ]
            for key in keys:
                del self._records[key]
            return len(keys)
