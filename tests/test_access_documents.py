"""CAS, coordination et reprise après écriture partielle du stockage Azure."""

import json
from types import SimpleNamespace

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
    ServiceRequestError,
)

from opti_route.access_documents import AzureBlobJsonStore, StorageConflict
from opti_route.access_requests import AccessRequests
from opti_route.auth import AuthenticatedUser
from opti_route.azure_storage import AzureBlobAppStore
from opti_route.flow_client import ResultatFlux
from opti_route.storage import StorageError, UserAccessProfile

OID = "11111111-1111-4111-8111-111111111111"
ADMIN = AuthenticatedUser("Admin", "entra", "admin", "33333333-3333-4333-8333-333333333333")


def modified():
    error = ResourceModifiedError("ConditionNotMet")
    error.status_code = 412
    return error


class Container:
    def __init__(self):
        self.values = {}
        self.etags = {}
        self.leased = False
        self.renewals = 0
        self.before_write = lambda name, value: None
        self.before_delete = lambda name: None

    def get_blob_client(self, name):
        return Blob(self, name)

    def upload_blob(self, name, data, **options):
        return self.get_blob_client(name).upload_blob(data, **options)

    def list_blobs(self, name_starts_with):
        return [SimpleNamespace(name=name) for name in sorted(self.values) if name.startswith(name_starts_with)]


class Blob:
    def __init__(self, container, name):
        self.container, self.name = container, name

    def upload_blob(self, data, **options):
        container = self.container
        container.before_write(self.name, json.loads(data))
        if self.name in container.values and not options.get("overwrite"):
            raise ResourceExistsError(status_code=409)
        if "etag" in options:
            assert options["match_condition"] == MatchConditions.IfNotModified
            if options["etag"] != container.etags.get(self.name):
                raise modified()
        container.values[self.name] = data
        container.etags[self.name] = str(int(container.etags.get(self.name, "0")) + 1)
        return {"etag": container.etags[self.name]}

    def get_blob_properties(self):
        if self.name not in self.container.values:
            raise ResourceNotFoundError(status_code=404)
        return SimpleNamespace(size=len(self.container.values[self.name]), etag=self.container.etags[self.name])

    def download_blob(self, **options):
        return SimpleNamespace(properties=self.get_blob_properties(), readall=lambda: self.container.values[self.name])

    def delete_blob(self, **options):
        self.container.before_delete(self.name)
        assert options["match_condition"] == MatchConditions.IfNotModified
        if options["etag"] != self.container.etags.get(self.name):
            raise modified()
        del self.container.values[self.name]
        del self.container.etags[self.name]

    def acquire_lease(self, lease_duration):
        assert lease_duration == 60
        if self.container.leased:
            raise ResourceExistsError(status_code=409)
        self.container.leased = True
        return SimpleNamespace(renew=self.renew, release=self.release)

    def renew(self):
        if not self.container.leased:
            raise modified()
        self.container.renewals += 1

    def release(self):
        self.container.leased = False


@pytest.fixture
def azure_service():
    store = AzureBlobAppStore.__new__(AzureBlobAppStore)
    store._container = Container()
    store._access_documents = None
    flow = SimpleNamespace(verifier=lambda request: ResultatFlux(200, "APPLIQUEE", "Confirmé", OID, {}, (), "test-run"))
    return AccessRequests(store, flow)


def test_azure_cas_applies_to_updates_and_deletes():
    documents = AzureBlobJsonStore(Container())
    key = "access-state/test.json"
    etag = documents.write(key, {"value": 1}, None)
    current = documents.write(key, {"value": 2}, etag)
    with pytest.raises(StorageConflict):
        documents.write(key, {"value": 3}, etag)
    with pytest.raises(StorageConflict):
        documents.delete(key, etag)
    assert documents.read(key) == ({"value": 2}, current)
    documents.delete(key, current)
    assert documents.read(key) == (None, None)


def test_azure_lease_coordinates_instances_and_nested_transactions():
    container = Container()
    first, second = AzureBlobJsonStore(container), AzureBlobJsonStore(container)
    with first.transaction(), first.transaction():
        with pytest.raises(StorageConflict), second.transaction():
            pytest.fail("Une seconde instance ne doit pas prendre le verrou.")
        first.write("access-state/test.json", {}, None)
    assert not container.leased
    assert container.renewals == 1
    with second.transaction():
        pass


def test_azure_profile_write_failure_leaves_prepared_proof_for_retry(azure_service):
    service = azure_service
    request = service.create(ADMIN, action="AJOUTER", group="ATC", nom="Dupont", prenom="Jean", scope={"atc_code": "31-02-004"})

    def fail_profile(name, value):
        if name.startswith("access-profiles/"):
            raise ServiceRequestError("Simulated outage")

    service.store._container.before_write = fail_profile
    with pytest.raises(StorageError):
        service.check(ADMIN, request["id"])
    pending = service.get(request["id"])
    assert pending["statut"] == "EN_ATTENTE"
    assert pending["application"]["proof"] == "AD"
    assert service.store.load_access_profile(OID) is None
    service.store._container.before_write = lambda name, value: None
    assert service.pending() == []  # Reprise sans nouvel appel réseau au flux.
    assert service.store.load_access_profile(OID).atc_code == "31-02-004"
    assert service.recent()[0]["statut"] == "CONFIRMEE"


def test_azure_interrupted_removal_creation_recovers_its_request(azure_service):
    service = azure_service
    service.store.save_access_profile(UserAccessProfile(OID, "Jean", "atc", "31-02-004"), updated_by="test")

    def fail_request(name, value):
        if name.startswith("access-requests/pending/"):
            raise ServiceRequestError("Simulated outage")

    service.store._container.before_write = fail_request
    with pytest.raises(StorageError):
        service.create(ADMIN, action="SUPPRIMER", group="ATC", principal_id=OID)
    assert service.blocked(OID)
    service.store._container.before_write = lambda name, value: None
    pending = service.pending()
    assert len(pending) == 1
    service.cancel(ADMIN, pending[0]["id"])
    assert not service.blocked(OID)
    assert service.pending() == []


def test_azure_partial_archive_is_retried_without_resurrecting_request(azure_service):
    service = azure_service
    request = service.create(ADMIN, action="AJOUTER", group="ADMIN", principal_id=OID)
    service.store._container.before_delete = lambda name: (_ for _ in ()).throw(ServiceRequestError("Simulated outage"))
    with pytest.raises(StorageError):
        service.check(ADMIN, request["id"])
    assert service.profile(OID)["role"] == "admin"
    assert service.get(request["id"])["statut"] == "CONFIRMEE"
    service.store._container.before_delete = lambda name: None
    assert service.pending() == []
    assert len(service.recent()) == 1


def test_azure_reclaim_retries_after_clients_are_renamed_but_profile_write_fails(azure_service):
    import pandas as pd

    from opti_route.data import standardize_clients

    service = azure_service
    clients = standardize_clients(pd.DataFrame({
        "client_id": ["A"], "client_name": ["Alpha"], "salesperson_code": ["164"],
        "salesperson": ["ATC Angers"], "address": ["1 rue du Test"], "postal_code": ["14000"], "city": ["Caen"],
    }))
    service.store.save_clients(clients, source_name="clients.xlsx", imported_by="test")
    record = service.catalog.save(ADMIN, group="ATC", city="Angers", scope={"atc_code": "164", "atc_name": "ATC Angers"})
    request = service.create(ADMIN, action="AJOUTER", group="ATC", nom="Dupont", prenom="Jean",
                             scope={"atc_code": "164"}, catalog_id=record["id"], rename_clients=True)

    def fail_profile(name, value):
        if name.startswith("access-profiles/"):
            raise ServiceRequestError("Simulated outage")

    service.store._container.before_write = fail_profile
    with pytest.raises(StorageError):
        service.check(ADMIN, request["id"])
    assert service.store.load_clients()[0].loc[0, "salesperson"] == "Dupont Jean"
    assert service.store.load_access_profile(OID) is None
    assert service.get(request["id"])["application"]["proof"] == "AD"
    service.store._container.before_write = lambda name, value: None
    assert service.pending() == []
    assert service.store.load_access_profile(OID).atc_name == "Dupont Jean"
    assert service.catalog.get(record["id"])["assigned_to"] == OID


def test_azure_departure_cancellation_recovers_after_interrupted_archive(azure_service):
    import pandas as pd

    from opti_route.data import standardize_clients

    service = azure_service
    clients = standardize_clients(pd.DataFrame({
        "client_id": ["A"], "client_name": ["Alpha"], "salesperson_code": ["164"],
        "salesperson": ["Jean"], "address": ["1 rue du Test"], "postal_code": ["14000"], "city": ["Caen"],
    }))
    service.store.save_clients(clients, source_name="clients.xlsx", imported_by="test")
    service.store.save_access_profile(UserAccessProfile(OID, "Jean", "atc", atc_code="164", atc_name="Jean"), updated_by="test")
    request = service.create(ADMIN, action="SUPPRIMER", group="ATC", principal_id=OID, vacant_city="Angers")

    def fail_cancelled(name, value):
        if name.startswith("access-requests/pending/") and value.get("statut") == "ANNULEE":
            raise ServiceRequestError("Simulated outage")

    service.store._container.before_write = fail_cancelled
    with pytest.raises(StorageError):
        service.cancel(ADMIN, request["id"])
    assert service.get(request["id"])["cancelling"]
    service.store._container.before_write = lambda name, value: None
    assert service.pending() == []
    assert not service.blocked(OID)
    assert service.store.load_clients()[0].loc[0, "salesperson"] == "Jean"
    assert service.catalog.entries() == []


def test_azure_rejects_oversized_documents_before_writing(azure_service, monkeypatch):
    monkeypatch.setattr("opti_route.access_documents.MAX_DOCUMENT_BYTES", 32)
    with pytest.raises(StorageError, match="volumineux"):
        azure_service.documents.write("access-scopes/too-big.json", {"name": "x" * 40}, None)
    assert azure_service.documents.read("access-scopes/too-big.json") == (None, None)


def test_azure_bootstrap_retries_after_partial_migration_without_creating_duplicates(azure_service):
    import pandas as pd

    from opti_route.data import standardize_clients
    from opti_route.scope_catalog import BOOTSTRAP_KEY

    service = azure_service
    portfolio = standardize_clients(pd.DataFrame({
        "client_id": ["A"], "client_name": ["Alpha"], "salesperson_code": ["164"],
        "salesperson": ["ATC164"], "address": ["1 rue du Test"], "postal_code": ["14000"], "city": ["Caen"],
    }))
    service.store.save_clients(portfolio, source_name="clients.xlsx", imported_by="test")

    def fail_marker(name, value):
        if name == BOOTSTRAP_KEY:
            raise ServiceRequestError("Simulated outage")

    service.store._container.before_write = fail_marker
    with pytest.raises(StorageError):
        service.catalog.bootstrap_existing(ADMIN)
    assert len(service.catalog.entries()) == 1
    service.store._container.before_write = lambda name, value: None
    service.catalog.bootstrap_existing(ADMIN)
    assert len(service.catalog.entries()) == 1
    assert service.catalog.entries()[0]["display_name"] == "ATC164"
    assert service.store.list_access_profiles() == []
