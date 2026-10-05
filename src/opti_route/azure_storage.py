from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime

import pandas as pd
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .storage import (
    PortfolioMetadata,
    RouteConfiguration,
    StorageError,
    UserAccessProfile,
    deserialize_portfolio,
    serialize_portfolio,
)

_PORTFOLIO_BLOB = "state/portfolio.json"
_SETTINGS_BLOB = "state/settings.json"
_ACCESS_PREFIX = "access-profiles/"
_MAX_PORTFOLIO_BLOB_BYTES = 150 * 1024 * 1024
_MAX_SMALL_BLOB_BYTES = 1024 * 1024


class AzureBlobAppStore:
    """Stockage persistant et privé pour Azure Container Apps.

    L'identité managée du conteneur doit disposer de ``Storage Blob Data
    Contributor``. Le conteneur est créé par l'infrastructure, pas par
    l'application, afin de conserver une séparation claire des privilèges.
    """

    def __init__(
        self,
        *,
        account_url: str | None,
        container_name: str,
        connection_string: str | None = None,
    ) -> None:
        if not container_name.strip():
            raise StorageError("AZURE_STORAGE_CONTAINER est obligatoire.")
        try:
            if connection_string:
                service = BlobServiceClient.from_connection_string(connection_string)
            else:
                if not account_url:
                    raise StorageError("AZURE_STORAGE_ACCOUNT_URL est obligatoire.")
                service = BlobServiceClient(
                    account_url=account_url.rstrip("/"),
                    credential=DefaultAzureCredential(),
                )
            self._container = service.get_container_client(container_name.strip())
            self._container.get_container_properties()
        except StorageError:
            raise
        except ResourceNotFoundError as exc:
            raise StorageError(
                "Le conteneur Azure Storage configuré n'existe pas. Créez-le via l'infrastructure."
            ) from exc
        except AzureError as exc:
            raise StorageError(
                "Connexion à Azure Storage impossible avec l'identité managée configurée."
            ) from exc

    def _read_document(self, blob_name: str) -> dict[str, object] | None:
        try:
            blob = self._container.get_blob_client(blob_name)
            properties = blob.get_blob_properties()
            maximum_size = (
                _MAX_PORTFOLIO_BLOB_BYTES
                if blob_name == _PORTFOLIO_BLOB
                else _MAX_SMALL_BLOB_BYTES
            )
            if properties.size > maximum_size:
                raise StorageError("Une donnée du stockage Azure dépasse la taille autorisée.")
            payload = blob.download_blob(max_concurrency=1).readall()
        except StorageError:
            raise
        except ResourceNotFoundError:
            return None
        except AzureError as exc:
            raise StorageError("Lecture du stockage Azure impossible.") from exc
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError("Une donnée du stockage Azure est corrompue.") from exc
        if not isinstance(document, dict):
            raise StorageError("Une donnée du stockage Azure est invalide.")
        return document

    def _write_document(self, blob_name: str, document: dict[str, object]) -> None:
        payload = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            self._container.upload_blob(
                name=blob_name,
                data=payload,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json; charset=utf-8"),
            )
        except AzureError as exc:
            raise StorageError("Écriture dans Azure Storage impossible.") from exc

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
        self._write_document(
            _PORTFOLIO_BLOB,
            {
                "schema_version": 1,
                "metadata": asdict(metadata),
                "payload": payload,
            },
        )
        return metadata

    def load_clients(self) -> tuple[pd.DataFrame | None, PortfolioMetadata | None]:
        document = self._read_document(_PORTFOLIO_BLOB)
        if document is None:
            return None, None
        try:
            metadata_document = document["metadata"]
            if not isinstance(metadata_document, dict):
                raise TypeError
            metadata = PortfolioMetadata(
                source_name=str(metadata_document["source_name"]),
                imported_at=str(metadata_document["imported_at"]),
                imported_by=str(metadata_document["imported_by"]),
                row_count=int(metadata_document["row_count"]),
                digest=str(metadata_document["digest"]),
            )
            payload = str(document["payload"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError("Le portefeuille Azure stocké est incomplet.") from exc
        return deserialize_portfolio(payload, metadata), metadata

    def load_route_configuration(self) -> RouteConfiguration:
        document = self._read_document(_SETTINGS_BLOB) or {}
        try:
            return RouteConfiguration(
                radius_km=int(document.get("radius_km", 30)),
                max_visits=int(document.get("max_visits", 10)),
                return_to_start=bool(document.get("return_to_start", True)),
            ).validated()
        except (TypeError, ValueError) as exc:
            raise StorageError("Les paramètres Azure stockés sont invalides.") from exc

    def save_route_configuration(self, configuration: RouteConfiguration) -> None:
        configuration.validated()
        self._write_document(
            _SETTINGS_BLOB,
            {
                "schema_version": 1,
                "radius_km": configuration.radius_km,
                "max_visits": configuration.max_visits,
                "return_to_start": configuration.return_to_start,
            },
        )

    @staticmethod
    def _profile_blob_name(principal_id: str) -> str:
        normalized_id = principal_id.strip().casefold()
        digest = hashlib.sha256(normalized_id.encode("utf-8")).hexdigest()
        return f"{_ACCESS_PREFIX}{digest}.json"

    @staticmethod
    def _profile_from_document(document: dict[str, object]) -> UserAccessProfile:
        agencies_value = document.get("agencies", [])
        if not isinstance(agencies_value, list):
            raise StorageError("Une habilitation Azure est invalide.")
        return UserAccessProfile(
            principal_id=str(document.get("principal_id", "")),
            display_name=str(document.get("display_name", "")),
            role=str(document.get("role", "")),
            atc_code=(
                str(document["atc_code"])
                if document.get("atc_code") is not None
                else None
            ),
            agencies=tuple(str(value) for value in agencies_value),
            updated_at=str(document.get("updated_at", "")),
            updated_by=str(document.get("updated_by", "")),
        ).validated()

    def list_access_profiles(self) -> list[UserAccessProfile]:
        try:
            names = [
                blob.name
                for blob in self._container.list_blobs(name_starts_with=_ACCESS_PREFIX)
                if blob.name.endswith(".json")
            ]
        except AzureError as exc:
            raise StorageError("Lecture des habilitations Azure impossible.") from exc
        profiles = []
        for name in names:
            document = self._read_document(name)
            if document is not None:
                profiles.append(self._profile_from_document(document))
        return sorted(
            profiles,
            key=lambda profile: (profile.display_name.casefold(), profile.principal_id),
        )

    def load_access_profile(self, principal_id: str) -> UserAccessProfile | None:
        normalized_id = principal_id.strip().casefold()
        if not normalized_id:
            return None
        document = self._read_document(self._profile_blob_name(normalized_id))
        if document is None:
            return None
        profile = self._profile_from_document(document)
        if profile.principal_id != normalized_id:
            raise StorageError("L'habilitation Azure stockée est incohérente.")
        return profile

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
        self._write_document(
            self._profile_blob_name(stored_profile.principal_id),
            {"schema_version": 1, **asdict(stored_profile)},
        )
        return stored_profile

    def delete_access_profile(self, principal_id: str) -> bool:
        normalized_id = principal_id.strip().casefold()
        if not normalized_id:
            raise StorageError("L'identifiant Entra est obligatoire.")
        try:
            self._container.delete_blob(
                self._profile_blob_name(normalized_id),
                delete_snapshots="include",
            )
            return True
        except ResourceNotFoundError:
            return False
        except AzureError as exc:
            raise StorageError("Suppression de l'habilitation Azure impossible.") from exc
