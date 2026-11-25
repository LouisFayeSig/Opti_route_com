"""Documents d'habilitation avec CAS ; coordination des opérations multi-documents."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from threading import RLock, local
from uuid import uuid4

from azure.core import MatchConditions
from azure.core.exceptions import (
    AzureError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
)

from .storage import StorageError

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


class StorageConflict(StorageError):
    pass


def validate_key(key):
    if not key.startswith(("access-requests/", "access-state/", "access-scopes/")) or ".." in key or "\\" in key:
        raise StorageError("Clé de document d'habilitation invalide.")


def guard_profile_write(store, principal_id, request_id):
    import hashlib

    for key in store.access_documents.keys("access-requests/portfolio-operations/pending/"):
        operation, _ = store.access_documents.read(key)
        if operation and operation["id"] != request_id and (operation.get("profile") or {}).get("principal_id") == principal_id:
            raise StorageConflict("Une écriture de périmètre doit être terminée avant de modifier ce profil.")

    digest = hashlib.sha256(principal_id.strip().casefold().encode("utf-8")).hexdigest()
    state, _ = store.access_documents.read(f"access-state/{digest}.json")
    owner = (state or {}).get("pending_request_id")
    if owner is not None and owner != request_id:
        raise StorageConflict("Une demande est en cours pour ce compte. Terminez-la avant de modifier ou réimporter son profil.")


def guard_portfolio_write(store, request_id):
    for key in store.access_documents.keys("access-requests/portfolio-operations/pending/"):
        operation, _ = store.access_documents.read(key)
        if operation and operation["id"] != request_id:
            raise StorageConflict("Une écriture de périmètre doit être terminée avant de modifier le listing.")
    if request_id is not None:
        return
    documents = store.access_documents
    for key in documents.keys("access-requests/pending/"):
        request, _ = documents.read(key)
        if request and request.get("statut") in {"EN_ATTENTE", "A_PRECISER"} and (request.get("portfolio_change") or request.get("vacancy")):
            raise StorageConflict("Une réaffectation de portefeuille est en cours. Terminez ou annulez la demande avant de réimporter les clients.")


class SQLiteJsonStore:
    def __init__(self, store):
        self.store = store
        with store._connection() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS access_documents (key TEXT PRIMARY KEY, payload TEXT NOT NULL, etag TEXT NOT NULL)")

    def transaction(self):
        return self.store.access_transaction()

    def read(self, key):
        validate_key(key)
        with self.store._connection() as connection:
            row = connection.execute("SELECT payload, etag FROM access_documents WHERE key=?", (key,)).fetchone()
        if row is None:
            return None, None
        try:
            document = json.loads(row[0])
            if not isinstance(document, dict):
                raise TypeError
        except (ValueError, TypeError):
            raise StorageError("Document d'habilitation invalide.") from None
        return document, row[1]

    def write(self, key, document, expected_etag):
        validate_key(key)
        etag = uuid4().hex
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False)
        if len(payload.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise StorageError("Document d'habilitation trop volumineux.")
        try:
            with self.store._connection() as connection:
                if expected_etag is None:
                    connection.execute("INSERT INTO access_documents VALUES (?, ?, ?)", (key, payload, etag))
                else:
                    result = connection.execute("UPDATE access_documents SET payload=?, etag=? WHERE key=? AND etag=?", (payload, etag, key, expected_etag))
                    if result.rowcount != 1:
                        raise StorageConflict("La demande a changé, actualisez l'affichage.")
        except sqlite3.IntegrityError:
            raise StorageConflict("Ce document existe déjà.") from None
        return etag

    def delete(self, key, expected_etag):
        validate_key(key)
        with self.store._connection() as connection:
            result = connection.execute("DELETE FROM access_documents WHERE key=? AND etag=?", (key, expected_etag))
            if result.rowcount != 1:
                raise StorageConflict("Le document a changé.")

    def keys(self, prefix):
        validate_key(prefix)
        with self.store._connection() as connection:
            rows = connection.execute("SELECT key FROM access_documents WHERE substr(key, 1, ?)=? ORDER BY key", (len(prefix), prefix)).fetchall()
        return [row[0] for row in rows]


class AzureBlobJsonStore:
    def __init__(self, container):
        self.container = container
        self._lock = RLock()
        self._local = local()

    @contextmanager
    def transaction(self):
        with self._lock:
            if getattr(self._local, "lease", None) is not None:
                yield
                return
            blob = self.container.get_blob_client("access-requests/.lock")
            try:
                try:
                    blob.upload_blob(b"{}", overwrite=False)
                except ResourceExistsError:
                    pass
                lease = blob.acquire_lease(lease_duration=60)
            except AzureError:
                raise StorageConflict("Une opération d'habilitation est en cours ou le stockage est indisponible. Réessayez.") from None
            self._local.lease = lease
            try:
                yield
            finally:
                self._local.lease = None
                try:
                    lease.release()
                except AzureError:
                    pass

    def _renew(self):
        lease = getattr(self._local, "lease", None)
        if lease is not None:
            try:
                lease.renew()
            except AzureError:
                raise StorageConflict("Le verrou d'habilitation a expiré. Réessayez.") from None

    def read(self, key):
        validate_key(key)
        try:
            download = self.container.get_blob_client(key).download_blob(max_concurrency=1)
            if download.properties.size > MAX_DOCUMENT_BYTES:
                raise StorageError("Document d'habilitation trop volumineux.")
            document = json.loads(download.readall().decode("utf-8"))
            if not isinstance(document, dict):
                raise StorageError("Document d'habilitation invalide.")
            return document, download.properties.etag
        except ResourceNotFoundError:
            return None, None
        except (AzureError, ValueError, UnicodeError):
            raise StorageError("Lecture des demandes d'habilitation impossible.") from None

    def write(self, key, document, expected_etag):
        validate_key(key)
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise StorageError("Document d'habilitation trop volumineux.")
        self._renew()
        options = {"overwrite": False} if expected_etag is None else {
            "overwrite": True, "etag": expected_etag, "match_condition": MatchConditions.IfNotModified,
        }
        try:
            result = self.container.get_blob_client(key).upload_blob(
                payload, **options,
            )
            return result["etag"]
        except HttpResponseError as exc:
            if exc.status_code in {409, 412}:
                raise StorageConflict("La demande a changé, actualisez l'affichage.") from None
            raise StorageError("Écriture des demandes d'habilitation impossible.") from None
        except AzureError:
            raise StorageError("Écriture des demandes d'habilitation impossible.") from None

    def delete(self, key, expected_etag):
        validate_key(key)
        self._renew()
        try:
            self.container.get_blob_client(key).delete_blob(etag=expected_etag, match_condition=MatchConditions.IfNotModified)
        except HttpResponseError as exc:
            if exc.status_code in {404, 409, 412}:
                raise StorageConflict("Le document a changé.") from None
            raise StorageError("Archivage de la demande impossible.") from None
        except AzureError:
            raise StorageError("Archivage de la demande impossible.") from None

    def keys(self, prefix):
        validate_key(prefix)
        try:
            return [blob.name for blob in self.container.list_blobs(name_starts_with=prefix) if blob.name.endswith(".json")]
        except AzureError:
            raise StorageError("Lecture de la liste des demandes impossible.") from None
