from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


class StorageError(RuntimeError):
    """Le stockage applicatif ne peut pas être lu ou mis à jour."""


@dataclass(frozen=True)
class RouteConfiguration:
    radius_km: int = 30
    max_visits: int = 10
    return_to_start: bool = True

    def validated(self) -> RouteConfiguration:
        if self.radius_km not in {10, 20, 30, 50, 100}:
            raise StorageError("Le rayon doit valoir 10, 20, 30, 50 ou 100 km.")
        if not 1 <= self.max_visits <= 10:
            raise StorageError("Le nombre de visites doit être compris entre 1 et 10.")
        return self


@dataclass(frozen=True)
class PortfolioMetadata:
    source_name: str
    imported_at: str
    imported_by: str
    row_count: int
    digest: str


_CLIENT_COLUMNS = (
    "client_id",
    "client_name",
    "salesperson",
    "address",
    "address_2",
    "address_3",
    "postal_code",
    "city",
    "country",
    "latitude",
    "longitude",
    "full_address",
)


class AppStore:
    """Stocke le portefeuille normalisé et les réglages dans un SQLite local.

    Le classeur importé n'est jamais conservé. Seules les colonnes normalisées nécessaires
    à la tournée sont sérialisées en JSON dans SQLite, via des requêtes paramétrées.
    """

    def __init__(self, path: Path):
        self.path = path
        parent_was_created = not self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if parent_was_created:
            self._set_private_permissions(self.path.parent, 0o700)
        self._initialize()

    @staticmethod
    def _set_private_permissions(path: Path, mode: int) -> None:
        if os.name != "posix":
            return
        try:
            os.chmod(path, mode)
        except OSError:
            # Les ACL Windows et certains volumes managés ne prennent pas en charge chmod.
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portfolio (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        payload TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        imported_at TEXT NOT NULL,
                        imported_by TEXT NOT NULL,
                        row_count INTEGER NOT NULL,
                        digest TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Initialisation du stockage impossible : {exc}") from exc
        self._set_private_permissions(self.path, 0o600)

    def save_clients(
        self,
        clients: pd.DataFrame,
        *,
        source_name: str,
        imported_by: str,
    ) -> PortfolioMetadata:
        if clients.empty:
            raise StorageError("Le portefeuille ne contient aucun client.")
        missing_columns = set(_CLIENT_COLUMNS).difference(clients.columns)
        if missing_columns:
            raise StorageError("Le portefeuille normalisé est incomplet.")
        if len(clients) > 50_000:
            raise StorageError("Le portefeuille dépasse la limite de 50 000 lignes.")

        salespeople = clients["salesperson"].fillna("").astype(str).str.strip()
        invalid_salespeople = salespeople.eq("") | salespeople.eq("Tous")
        if invalid_salespeople.any():
            raise StorageError(
                "Chaque client doit être affecté à un commercial avant l'enregistrement."
            )

        normalized = clients.loc[:, _CLIENT_COLUMNS].copy()
        payload = normalized.to_json(orient="records", force_ascii=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        safe_source_name = Path(source_name.replace("\\", "/")).name
        metadata = PortfolioMetadata(
            source_name=safe_source_name[:255] or "portefeuille",
            imported_at=datetime.now(UTC).isoformat(timespec="seconds"),
            imported_by=imported_by[:255],
            row_count=len(normalized),
            digest=digest,
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO portfolio (
                        singleton, payload, source_name, imported_at, imported_by, row_count, digest
                    ) VALUES (1, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        payload = excluded.payload,
                        source_name = excluded.source_name,
                        imported_at = excluded.imported_at,
                        imported_by = excluded.imported_by,
                        row_count = excluded.row_count,
                        digest = excluded.digest
                    """,
                    (
                        payload,
                        metadata.source_name,
                        metadata.imported_at,
                        metadata.imported_by,
                        metadata.row_count,
                        metadata.digest,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Enregistrement du portefeuille impossible : {exc}") from exc
        return metadata

    def load_clients(self) -> tuple[pd.DataFrame | None, PortfolioMetadata | None]:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT payload, source_name, imported_at, imported_by, row_count, digest
                    FROM portfolio WHERE singleton = 1
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Lecture du portefeuille impossible : {exc}") from exc
        if row is None:
            return None, None

        payload, source_name, imported_at, imported_by, row_count, digest = row
        if (
            not isinstance(payload, str)
            or hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest
        ):
            raise StorageError("Le portefeuille stocké est incohérent ou corrompu.")
        try:
            records = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise StorageError("Le portefeuille stocké n'est pas lisible.") from exc
        clients = pd.DataFrame.from_records(records, columns=_CLIENT_COLUMNS)
        clients["latitude"] = pd.to_numeric(clients["latitude"], errors="coerce")
        clients["longitude"] = pd.to_numeric(clients["longitude"], errors="coerce")
        metadata = PortfolioMetadata(
            source_name=str(source_name),
            imported_at=str(imported_at),
            imported_by=str(imported_by),
            row_count=int(row_count),
            digest=str(digest),
        )
        if len(clients) != metadata.row_count:
            raise StorageError("Le nombre de lignes du portefeuille stocké est incohérent.")
        return clients, metadata

    def load_route_configuration(self) -> RouteConfiguration:
        try:
            with self._connection() as connection:
                values = dict(connection.execute("SELECT key, value FROM app_settings").fetchall())
            return RouteConfiguration(
                radius_km=int(values.get("radius_km", "30")),
                max_visits=int(values.get("max_visits", "10")),
                return_to_start=values.get("return_to_start", "true").casefold() == "true",
            ).validated()
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise StorageError(f"Lecture des paramètres impossible : {exc}") from exc

    def save_route_configuration(self, configuration: RouteConfiguration) -> None:
        configuration.validated()
        values = {
            "radius_km": str(configuration.radius_km),
            "max_visits": str(configuration.max_visits),
            "return_to_start": str(configuration.return_to_start).casefold(),
        }
        try:
            with self._connection() as connection:
                connection.executemany(
                    """
                    INSERT INTO app_settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    values.items(),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Enregistrement des paramètres impossible : {exc}") from exc
