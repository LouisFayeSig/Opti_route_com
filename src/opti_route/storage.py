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
from typing import Protocol

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


@dataclass(frozen=True)
class UserAccessProfile:
    principal_id: str
    display_name: str
    role: str
    atc_code: str | None = None
    agencies: tuple[str, ...] = ()
    updated_at: str = ""
    updated_by: str = ""

    def validated(self) -> UserAccessProfile:
        principal_id = self.principal_id.strip().casefold()
        display_name = self.display_name.strip()
        role = self.role.strip().casefold()
        atc_code = (self.atc_code or "").strip() or None
        agencies = tuple(
            dict.fromkeys(value.strip() for value in self.agencies if value.strip())
        )
        if not principal_id or len(principal_id) > 255:
            raise StorageError("L'identifiant Entra est obligatoire et limite a 255 caracteres.")
        if role not in {"atc", "director"}:
            raise StorageError("Une habilitation doit avoir le role ATC ou directeur.")
        if role == "atc" and not atc_code:
            raise StorageError("Un code ATC est obligatoire pour le role ATC.")
        if role == "director" and not agencies:
            raise StorageError("Au moins une agence est obligatoire pour le role directeur.")
        return UserAccessProfile(
            principal_id=principal_id,
            display_name=display_name or principal_id,
            role=role,
            atc_code=atc_code if role == "atc" else None,
            agencies=agencies if role == "director" else (),
            updated_at=self.updated_at,
            updated_by=self.updated_by,
        )


_CLIENT_COLUMNS = (
    "client_id",
    "client_name",
    "salesperson_code",
    "salesperson",
    "agency",
    "agency_address",
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


def serialize_portfolio(
    clients: pd.DataFrame,
    *,
    source_name: str,
    imported_by: str,
) -> tuple[str, PortfolioMetadata]:
    if clients.empty:
        raise StorageError("Le portefeuille ne contient aucun client.")
    clients = clients.copy()
    if "salesperson_code" not in clients.columns and "salesperson" in clients.columns:
        clients["salesperson_code"] = clients["salesperson"]
    missing_columns = set(_CLIENT_COLUMNS).difference(clients.columns)
    if missing_columns:
        raise StorageError("Le portefeuille normalisé est incomplet.")
    if len(clients) > 70_000:
        raise StorageError("Le portefeuille dépasse la limite de 70 000 lignes.")

    salespeople = clients["salesperson"].fillna("").astype(str).str.strip()
    invalid_salespeople = salespeople.eq("") | salespeople.eq("Tous")
    if invalid_salespeople.any():
        raise StorageError(
            "Chaque client doit être affecté à un commercial avant l'enregistrement."
        )
    salesperson_codes = clients["salesperson_code"].fillna("").astype(str).str.strip()
    if salesperson_codes.eq("").any():
        raise StorageError("Chaque client doit être affecté à un code ATC.")

    normalized = clients.loc[:, _CLIENT_COLUMNS].copy()
    payload = normalized.to_json(orient="records", force_ascii=False)
    metadata = PortfolioMetadata(
        source_name=Path(source_name.replace("\\", "/")).name[:255] or "portefeuille",
        imported_at=datetime.now(UTC).isoformat(timespec="seconds"),
        imported_by=imported_by[:255],
        row_count=len(normalized),
        digest=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )
    return payload, metadata


def deserialize_portfolio(payload: str, metadata: PortfolioMetadata) -> pd.DataFrame:
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != metadata.digest:
        raise StorageError("Le portefeuille stocké est incohérent ou corrompu.")
    try:
        records = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StorageError("Le portefeuille stocké n'est pas lisible.") from exc
    clients = pd.DataFrame.from_records(records, columns=_CLIENT_COLUMNS)
    clients["salesperson_code"] = (
        clients["salesperson_code"]
        .fillna(clients["salesperson"])
        .astype("string")
        .str.strip()
    )
    clients["latitude"] = pd.to_numeric(clients["latitude"], errors="coerce")
    clients["longitude"] = pd.to_numeric(clients["longitude"], errors="coerce")
    if len(clients) != metadata.row_count:
        raise StorageError("Le nombre de lignes du portefeuille stocké est incohérent.")
    return clients


class ApplicationStore(Protocol):
    def save_clients(
        self,
        clients: pd.DataFrame,
        *,
        source_name: str,
        imported_by: str,
    ) -> PortfolioMetadata: ...

    def load_clients(self) -> tuple[pd.DataFrame | None, PortfolioMetadata | None]: ...

    def load_route_configuration(self) -> RouteConfiguration: ...

    def save_route_configuration(self, configuration: RouteConfiguration) -> None: ...

    def list_access_profiles(self) -> list[UserAccessProfile]: ...

    def load_access_profile(self, principal_id: str) -> UserAccessProfile | None: ...

    def save_access_profile(
        self, profile: UserAccessProfile, *, updated_by: str
    ) -> UserAccessProfile: ...

    def delete_access_profile(self, principal_id: str) -> bool: ...


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
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS access_profiles (
                        principal_id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('atc', 'director')),
                        atc_code TEXT,
                        agencies TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        updated_by TEXT NOT NULL
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
        payload, metadata = serialize_portfolio(
            clients,
            source_name=source_name,
            imported_by=imported_by,
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
        if not isinstance(payload, str):
            raise StorageError("Le portefeuille stocké est incohérent ou corrompu.")
        metadata = PortfolioMetadata(
            source_name=str(source_name),
            imported_at=str(imported_at),
            imported_by=str(imported_by),
            row_count=int(row_count),
            digest=str(digest),
        )
        return deserialize_portfolio(payload, metadata), metadata

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

    def list_access_profiles(self) -> list[UserAccessProfile]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT principal_id, display_name, role, atc_code, agencies,
                           updated_at, updated_by
                    FROM access_profiles
                    ORDER BY display_name COLLATE NOCASE, principal_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"Lecture des habilitations impossible : {exc}") from exc
        profiles: list[UserAccessProfile] = []
        for row in rows:
            profiles.append(self._access_profile_from_row(row))
        return profiles

    def load_access_profile(self, principal_id: str) -> UserAccessProfile | None:
        normalized_id = principal_id.strip()
        if not normalized_id:
            return None
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT principal_id, display_name, role, atc_code, agencies,
                           updated_at, updated_by
                    FROM access_profiles WHERE principal_id = ? COLLATE NOCASE
                    """,
                    (normalized_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"Lecture de l'habilitation impossible : {exc}") from exc
        return self._access_profile_from_row(row) if row is not None else None

    @staticmethod
    def _access_profile_from_row(row: tuple[object, ...]) -> UserAccessProfile:
        try:
            parsed_agencies = json.loads(str(row[4]))
            if not isinstance(parsed_agencies, list):
                raise TypeError
            agencies = tuple(str(value) for value in parsed_agencies)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StorageError("Une habilitation stockée est corrompue.") from exc
        return UserAccessProfile(
            principal_id=str(row[0]),
            display_name=str(row[1]),
            role=str(row[2]),
            atc_code=str(row[3]) if row[3] is not None else None,
            agencies=agencies,
            updated_at=str(row[5]),
            updated_by=str(row[6]),
        ).validated()

    def save_access_profile(
        self, profile: UserAccessProfile, *, updated_by: str
    ) -> UserAccessProfile:
        profile = profile.validated()
        stored_profile = UserAccessProfile(
            principal_id=profile.principal_id,
            display_name=profile.display_name,
            role=profile.role,
            atc_code=profile.atc_code,
            agencies=profile.agencies,
            updated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            updated_by=updated_by.strip()[:255] or "Administrateur",
        )
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO access_profiles (
                        principal_id, display_name, role, atc_code, agencies,
                        updated_at, updated_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(principal_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        role = excluded.role,
                        atc_code = excluded.atc_code,
                        agencies = excluded.agencies,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by
                    """,
                    (
                        stored_profile.principal_id,
                        stored_profile.display_name,
                        stored_profile.role,
                        stored_profile.atc_code,
                        json.dumps(stored_profile.agencies, ensure_ascii=False),
                        stored_profile.updated_at,
                        stored_profile.updated_by,
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(f"Enregistrement de l'habilitation impossible : {exc}") from exc
        return stored_profile

    def delete_access_profile(self, principal_id: str) -> bool:
        normalized_id = principal_id.strip()
        if not normalized_id:
            raise StorageError("L'identifiant Entra est obligatoire.")
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    "DELETE FROM access_profiles WHERE principal_id = ? COLLATE NOCASE",
                    (normalized_id,),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise StorageError(f"Suppression de l'habilitation impossible : {exc}") from exc
