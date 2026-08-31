from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class CachedLocation:
    latitude: float
    longitude: float
    formatted_address: str
    provider: str


class GeocodeCache:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def _key(address: str) -> str:
        normalized = " ".join(address.casefold().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS geocodes (
                    address_hash TEXT PRIMARY KEY,
                    raw_address TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    formatted_address TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, address: str) -> CachedLocation | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT latitude, longitude, formatted_address, provider
                FROM geocodes WHERE address_hash = ?
                """,
                (self._key(address),),
            ).fetchone()
        return CachedLocation(*row) if row else None

    def set(
        self,
        address: str,
        latitude: float,
        longitude: float,
        formatted_address: str,
        provider: str = "Azure Maps",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO geocodes (
                    address_hash, raw_address, latitude, longitude,
                    formatted_address, provider, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(address_hash) DO UPDATE SET
                    latitude = excluded.latitude,
                    longitude = excluded.longitude,
                    formatted_address = excluded.formatted_address,
                    provider = excluded.provider,
                    updated_at = excluded.updated_at
                """,
                (
                    self._key(address),
                    address,
                    latitude,
                    longitude,
                    formatted_address,
                    provider,
                    datetime.now(UTC).isoformat(),
                ),
            )
