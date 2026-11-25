import pytest
from test_scope_catalog import ADMIN, OID, OTHER, clients, entry
from test_scope_catalog import service as _catalog_service

from opti_route.access_documents import StorageConflict
from opti_route.portfolio_operations import portfolios, profile_value
from opti_route.scope_catalog import alias_key
from opti_route.storage import StorageError, UserAccessProfile


@pytest.fixture
def service():
    yield from _catalog_service.__wrapped__()


def update(service, profile, **kwargs):
    return service.operations.update(
        ADMIN,
        profile.principal_id,
        expected_digest=service.store.load_clients()[1].digest,
        expected_profile=profile_value(profile),
        **kwargs,
    )


def test_old_shared_code_and_alias_mismatch_are_repaired_and_reclaimed(service):
    data = clients()
    data["salesperson"] = ["Atc BORDEAUX", "Atc BREST", "Atc MARSEILLE"]
    data["salesperson_code"] = "13-01-05"
    service.store.save_clients(data, source_name="legacy.xlsx", imported_by="test")
    bordeaux = service.catalog.put(
        {
            "id": "bordeaux",
            "group": "ATC",
            "scope": {"atc_code": "24-01-33", "atc_name": "BORDEAUX"},
            "display_name": "ATC BORDEAUX",
            "available": False,
            "assigned_to": OID,
            "origin": "Administration",
        }
    )
    service.catalog.put(
        {
            "id": "brest",
            "group": "ATC",
            "scope": {"atc_code": "22-05-03", "atc_name": "BREST"},
            "display_name": "ATC Atc BREST",
            "available": True,
            "origin": "Administration",
        }
    )
    service.store.save_access_profile(
        UserAccessProfile(
            OID,
            "Moreno Laetitia",
            "atc",
            atc_code="24-01-33",
            atc_name="Moreno Laetitia",
            atc_email="moreno@example.test",
        ),
        updated_by="test",
    )
    assert service.operations.repair_catalog(ADMIN) == 2
    result = service.store.load_clients()[0]
    assert result["salesperson_code"].tolist() == ["24-01-33", "22-05-03", "13-01-05"]
    assert result["salesperson"].tolist() == ["Moreno Laetitia", "ATC Atc BREST", "Atc MARSEILLE"]
    assert result["rag_email"].tolist() == data["rag_email"].tolist()
    assert result["ug"].tolist() == ["24", "22", "40"]
    assert result["agency"].tolist()[:2] == ["24-01", "22-05"]
    assert service.operations.repair_catalog(ADMIN) == 0
    assert service.catalog.get(bordeaux["id"])["assigned_to"] == OID


def test_catalog_code_edit_writes_only_named_portfolio_even_with_shared_code(service):
    record = entry(service)
    data = service.store.load_clients()[0]
    data["salesperson_code"] = "13-01-05"
    service.store.save_clients(data, source_name="legacy.xlsx", imported_by="test")
    service.catalog.save(
        ADMIN, group="ATC", city="Bordeaux", identity=record["id"], scope={"atc_code": "24-01-33"}
    )
    result = service.store.load_clients()[0]
    assert result["salesperson_code"].tolist() == ["24-01-33", "24-01-33", "13-01-05"]
    assert service.catalog.get(record["id"])["scope"]["atc_name"] == "ATC Bordeaux"


def test_atc_code_and_rag_edit_write_clients_and_keep_role(service):
    p = service.store.save_access_profile(
        UserAccessProfile(OID, "Jean", "atc", atc_code="164", atc_name="ATC Bordeaux"),
        updated_by="test",
    )
    update(service, p, atc_code="31-02-004", rag_email="autre.rag@example.test")
    result = service.store.load_clients()[0]
    assert result.loc[:1, "salesperson_code"].eq("31-02-004").all()
    assert result.loc[:1, "rag_email"].eq("autre.rag@example.test").all()
    assert result.loc[:1, "rag_name"].eq("Autre RAG").all()
    assert result.loc[2, "salesperson_code"] == "999"
    assert service.store.load_access_profile(OID).role == "atc"


def test_rag_can_remove_and_add_portfolios_without_changing_identity(service):
    p = service.store.save_access_profile(
        UserAccessProfile(OID, "Manager", "rag", rag_email="rag@example.test"), updated_by="test"
    )
    choices = portfolios(service.store.load_clients()[0])
    other = next(k for k, v in choices.items() if v["clients"]["client_id"].tolist() == ["C"])
    update(service, p, rag_portfolios=[other])
    result = service.store.load_clients()[0]
    assert result.loc[:1, "rag_email"].eq("").all()
    assert result.loc[2, "rag_email"] == "rag@example.test"
    assert result.loc[2, "rag_name"] == "Manager"
    assert service.store.load_access_profile(OID).rag_email == "rag@example.test"


def test_dr_changes_ugs_without_changing_physical_ugs_and_preserves_other_controller(service):
    p = service.store.save_access_profile(
        UserAccessProfile(OID, "DR Jean", "dr", ugs=("20",)), updated_by="test"
    )
    service.store.save_access_profile(
        UserAccessProfile(OTHER, "DR Autre", "dr", ugs=("20",)), updated_by="test"
    )
    update(service, p, ugs=["40"])
    result = service.store.load_clients()[0]
    assert result["ug"].tolist() == ["20", "20", "40"]
    assert result["dr_name"].tolist() == ["DR Autre", "DR Autre", "DR Jean"]
    assert service.store.load_access_profile(OID).ugs == ("40",)


def test_stale_admin_cannot_overwrite_a_newer_change(service):
    p = service.store.save_access_profile(
        UserAccessProfile(OID, "Jean", "atc", atc_code="164", atc_name="ATC Bordeaux"),
        updated_by="test",
    )
    digest = service.store.load_clients()[1].digest
    update(service, p, atc_code="31-02-004")
    with pytest.raises(StorageConflict, match="changé"):
        service.operations.update(
            ADMIN,
            OID,
            expected_digest=digest,
            expected_profile=profile_value(p),
            atc_code="99-01-001",
        )
    assert service.store.load_clients()[0].loc[0, "salesperson_code"] == "31-02-004"


def test_assigned_catalog_tracks_its_owners_new_code(service):
    record = entry(service)
    service.catalog.put({**record, "assigned_to": OID, "available": False})
    p = service.store.save_access_profile(
        UserAccessProfile(OID, "Jean", "atc", atc_code="164", atc_name="ATC Bordeaux"),
        updated_by="test",
    )
    update(service, p, atc_code="31-02-004")
    assert service.catalog.get(record["id"])["scope"]["atc_code"] == "31-02-004"
    assert service.operations.repair_catalog(ADMIN) == 0


def test_pending_ad_assignment_prevents_a_conflicting_scope_edit(service):
    record = entry(service)
    service.create(
        ADMIN,
        action="AJOUTER",
        group="ATC",
        nom="Nouveau",
        prenom="Titulaire",
        catalog_id=record["id"],
        rename_clients=True,
        scope={"atc_code": "164"},
    )
    p = service.store.save_access_profile(
        UserAccessProfile(OTHER, "Jean", "atc", atc_code="164", atc_name="ATC Bordeaux"),
        updated_by="test",
    )
    with pytest.raises(StorageConflict, match="demande AD"):
        update(service, p, atc_code="31-02-004")
    assert service.store.load_clients()[0].loc[0, "salesperson_code"] == "164"


def test_stale_catalog_form_does_not_override_another_admin(service):
    record = entry(service)
    service.catalog.save(
        ADMIN,
        group="ATC",
        city="Bordeaux",
        identity=record["id"],
        scope={"atc_code": "24-01-33"},
        expected=record,
    )
    with pytest.raises(StorageConflict, match="autre administrateur"):
        service.catalog.save(
            ADMIN,
            group="ATC",
            city="Bordeaux",
            identity=record["id"],
            scope={"atc_code": "13-01-05"},
            expected=record,
        )


def test_ad_reclaim_without_clients_is_rejected(service):
    record = entry(service)
    service.catalog.put(
        {
            **record,
            "scope": {"atc_code": "99999", "atc_name": "Inconnu"},
            "display_name": "ATC Inconnu",
        }
    )
    with pytest.raises(ValueError, match="Aucun client"):
        service.create(
            ADMIN,
            action="AJOUTER",
            group="ATC",
            nom="Moreno",
            prenom="Laetitia",
            catalog_id=record["id"],
            rename_clients=True,
            scope={"atc_code": "99999"},
        )
    assert not service.pending()


def test_interrupted_blob_style_operation_recovers_idempotently(service, monkeypatch):
    from contextlib import nullcontext

    # Simule Azure : les écritures ne sont pas annulées comme une transaction SQLite.
    monkeypatch.setattr(service.documents, "transaction", lambda: nullcontext())
    p = service.store.save_access_profile(
        UserAccessProfile(OID, "Jean", "atc", atc_code="164", atc_name="ATC Bordeaux"),
        updated_by="test",
    )
    save = service.store.save_access_profile
    monkeypatch.setattr(
        service.store,
        "save_access_profile",
        lambda *a, **k: (_ for _ in ()).throw(StorageError("interruption")),
    )
    with pytest.raises(StorageError, match="interruption"):
        update(service, p, atc_code="31-02-004")
    assert service.store.load_clients()[0].loc[0, "salesperson_code"] == "31-02-004"
    assert service.documents.keys("access-requests/portfolio-operations/pending/")
    with pytest.raises(StorageConflict):
        service.store.save_clients(clients(), source_name="import.xlsx", imported_by="test")
    monkeypatch.setattr(service.store, "save_access_profile", save)
    service.operations.recover(ADMIN)
    service.operations.recover(ADMIN)
    assert service.store.load_access_profile(OID).atc_code == "31-02-004"
    assert not service.documents.keys("access-requests/portfolio-operations/pending/")
    assert len(service.documents.keys("access-requests/portfolio-operations/done/")) == 1


@pytest.mark.parametrize("value", ["ATC Bordeaux", "Atc BORDEAUX", "ATC Atc BORDEAUX", "BORDEAUX"])
def test_alias_normalization(value):
    assert alias_key(value) == "bordeaux"
