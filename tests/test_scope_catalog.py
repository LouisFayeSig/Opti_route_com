from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest

from opti_route.access import AccessDeniedError, authorize_portfolio
from opti_route.access_documents import StorageConflict
from opti_route.access_requests import AccessRequests
from opti_route.auth import AuthenticatedUser
from opti_route.data import standardize_clients
from opti_route.flow_client import ResultatFlux, build_flow_payload
from opti_route.scope_catalog import business_scope, unassigned_from_users
from opti_route.storage import AppStore, StorageError, UserAccessProfile

OID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ADMIN = AuthenticatedUser("Admin", "entra", "admin", "33333333-3333-4333-8333-333333333333")


def clients():
    return standardize_clients(
        pd.DataFrame(
            {
                "client_id": ["A", "B", "C"],
                "client_name": ["Alpha", "Beta", "Gamma"],
                "salesperson": ["ATC Bordeaux", "ATC Bordeaux", "Autre ATC"],
                "salesperson_code": ["164", "164", "999"],
                "salesperson_email": [
                    "vacant@example.test",
                    "vacant@example.test",
                    "autre@example.test",
                ],
                "rag_name": ["RAG Lyon", "RAG Lyon", "Autre RAG"],
                "rag_email": ["rag@example.test", "rag@example.test", "autre.rag@example.test"],
                "ug": ["20", "20", "40"],
                "agency": ["20-01", "20-01", "40-01"],
                "address": ["1 rue du Test"] * 3,
                "postal_code": ["14000"] * 3,
                "city": ["Caen"] * 3,
                "latitude": [49.18] * 3,
                "longitude": [-0.37] * 3,
            }
        )
    )


def users():
    return pd.DataFrame(
        {
            "Rôle Entra": ["ATC", "RAG", "DR", "ADMIN", "ATC"],
            "Nom affiché": ["ATC Bordeaux", "RAG Lyon", "DR Nantes", "ADMIN Paris", "Compte connu"],
            "Object ID": [None, "", None, None, OID],
            "Code ATC": ["164", "", "", "", "999"],
            "UPN ou e-mail": [
                "vacant@example.test",
                "rag@example.test",
                "",
                "",
                "autre@example.test",
            ],
            "Code Region": ["", "", "20", "", ""],
        }
    )


@pytest.fixture
def service():
    path = Path(".cache") / f"catalog-test-{uuid4().hex}.sqlite3"
    store = AppStore(path)
    store.save_clients(clients(), source_name="clients.xlsx", imported_by="test")
    calls = []

    def verify(request):
        calls.append(request)
        return ResultatFlux(
            200, "APPLIQUEE", "Confirmé", request.get("objectId") or OID, {}, (), "test-run"
        )

    value = AccessRequests(store, SimpleNamespace(verifier=verify, calls=calls))
    yield value
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def entry(service, group="ATC"):
    service.catalog.import_users(ADMIN, users(), service.store.load_clients()[0])
    return next(value for value in service.catalog.entries() if value["group"] == group)


def add(service, record, **changes):
    group = record["group"]
    scope = {**record["scope"]}
    if group == "ATC":
        scope["atc_email"] = "jean@example.test"
    if group == "RAG":
        scope["rag_email"] = "jean@example.test"
    return service.create(
        ADMIN,
        **{
            "action": "AJOUTER",
            "group": group,
            "nom": "Dupont",
            "prenom": "Jean",
            "catalog_id": record["id"],
            "rename_clients": True,
            "scope": scope,
            **changes,
        },
    )


def test_import_keeps_all_three_unidentified_populations_without_creating_accounts(service):
    assert service.catalog.import_users(ADMIN, users(), clients()) == 3
    assert {value["group"] for value in service.catalog.entries()} == {"ATC", "RAG", "DR"}
    assert service.store.list_access_profiles() == []
    assert all("principal_id" not in value for value in service.catalog.entries())
    service.catalog.import_users(ADMIN, users(), clients())
    assert len(service.catalog.entries()) == 3
    with pytest.raises(AccessDeniedError):
        authorize_portfolio(clients(), AuthenticatedUser("Jean", "entra", "atc", OID), None)


def test_rag_email_can_be_inferred_from_matching_manager_and_legacy_atc_code_is_retained():
    data = users()
    data.loc[1, "UPN ou e-mail"] = ""
    portfolio = clients()
    portfolio.loc[:1, "salesperson_code"] = "ATC Bordeaux"
    records = unassigned_from_users(data, portfolio)
    assert (
        next(value for value in records if value["group"] == "RAG")["scope"]["rag_email"]
        == "rag@example.test"
    )
    assert (
        next(value for value in records if value["group"] == "ATC")["scope"]["atc_code"]
        == "ATC Bordeaux"
    )


def test_imported_atc_template_does_not_expand_to_other_aliases_sharing_an_email(service):
    portfolio = clients()
    portfolio.loc[2, "salesperson_email"] = "vacant@example.test"
    service.store.save_clients(portfolio, source_name="clients.xlsx", imported_by="test")
    record = entry(service)
    request = add(service, record)
    service.check(ADMIN, request["id"])
    after = service.store.load_clients()[0]
    assert after.loc[2, "salesperson"] == "Autre ATC"
    assert after.loc[2, "salesperson_email"] == "vacant@example.test"


@pytest.mark.parametrize(
    "group,field", [("ATC", "salesperson"), ("RAG", "rag_name"), ("DR", "dr_name")]
)
def test_reclaim_renames_only_selected_clients_after_ad_proof(service, group, field):
    record = entry(service, group)
    request = add(service, record)
    before = service.store.load_clients()[0]
    assert not before[field].fillna("").eq("Dupont Jean").any()
    assert service.store.load_access_profile(OID) is None
    assert not {"scope", "catalog_id", "portfolio_change"} & build_flow_payload(request).keys()
    service.check(ADMIN, request["id"])
    after = service.store.load_clients()[0]
    assert after.loc[:1, field].tolist() == ["Dupont Jean"] * 2
    assert after.loc[2, field] == before.loc[2, field] or (
        pd.isna(after.loc[2, field]) and pd.isna(before.loc[2, field])
    )
    assert service.catalog.get(record["id"])["assigned_to"] == OID
    profile = service.store.load_access_profile(OID)
    authorized = authorize_portfolio(
        after, AuthenticatedUser("Jean", "entra", group.lower(), OID), profile
    )
    assert authorized.clients["client_id"].tolist() == ["A", "B"]
    service.check(ADMIN, request["id"])
    assert len(service.flow.calls) == 1


def test_same_group_reclaim_for_an_existing_account_is_direct_without_ad(service):
    record = entry(service)
    service.store.save_access_profile(
        UserAccessProfile(OID, "Jean", "atc", atc_code="999", atc_email="autre@example.test"),
        updated_by="test",
    )
    request = add(service, record, action="MODIFIER", principal_id=OID)
    assert request["action"] == "MODIFIER_CODE"
    assert request["resolved_by"] == "ADMIN"
    assert service.store.load_clients()[0].loc[:1, "salesperson"].eq("Dupont Jean").all()
    assert service.flow.calls == []


@pytest.mark.parametrize(
    "group,field", [("ATC", "salesperson"), ("RAG", "rag_name"), ("DR", "dr_name")]
)
def test_departure_renames_to_role_city_disables_account_and_cancellation_restores_data(
    service, group, field
):
    record = entry(service, group)
    service.store.save_access_profile(
        UserAccessProfile(OID, "Jean", group.lower(), **record["scope"]), updated_by="test"
    )
    original = service.store.load_clients()[0]
    request = service.create(
        ADMIN, action="SUPPRIMER", group=group, principal_id=OID, vacant_city=" Angers "
    )
    assert service.blocked(OID)
    assert service.store.load_clients()[0].loc[:1, field].eq(f"{group} Angers").all()
    vacancy = service.catalog.get(request["id"])
    assert vacancy["display_name"] == f"{group} Angers"
    assert vacancy["available"] is False
    with pytest.raises(ValueError, match="disponible"):
        add(service, vacancy)
    service.cancel(ADMIN, request["id"])
    assert not service.blocked(OID)
    pd.testing.assert_series_equal(
        service.store.load_clients()[0][field], original[field], check_dtype=False
    )
    assert request["id"] not in {value["id"] for value in service.catalog.entries()}


def test_completed_departure_can_be_reclaimed_by_a_different_person(service):
    record = entry(service)
    service.store.save_access_profile(
        UserAccessProfile(OID, "Ancien", "atc", **record["scope"]), updated_by="test"
    )
    departure = service.create(
        ADMIN, action="SUPPRIMER", group="ATC", principal_id=OID, vacant_city="Bordeaux"
    )
    service.check(ADMIN, departure["id"])
    vacancy = service.catalog.get(departure["id"])
    assert vacancy["available"]
    request = add(service, vacancy, principal_id=OTHER)
    service.check(ADMIN, request["id"])
    assert service.blocked(OID)
    assert service.store.load_access_profile(OID) is not None
    assert service.store.load_access_profile(OTHER).atc_name == "Dupont Jean"
    assert service.store.load_clients()[0].loc[:1, "salesperson"].eq("Dupont Jean").all()


def test_rag_reclaim_also_updates_own_atc_portfolio_without_changing_its_manager(service):
    portfolio = clients()
    portfolio.loc[2, "salesperson_email"] = "rag@example.test"
    service.store.save_clients(portfolio, source_name="clients.xlsx", imported_by="test")
    record = entry(service, "RAG")
    request = add(service, record)
    service.check(ADMIN, request["id"])
    result = service.store.load_clients()[0]
    assert result.loc[2, "salesperson"] == "Dupont Jean"
    assert result.loc[2, "salesperson_email"] == "jean@example.test"
    assert result.loc[2, "rag_name"] == "Autre RAG"
    assert result.loc[2, "rag_email"] == "autre.rag@example.test"


def test_pending_reclaim_blocks_reimport_duplicate_reclaim_and_catalog_changes(service):
    record = entry(service)
    request = add(service, record)
    with pytest.raises(StorageConflict):
        service.store.save_clients(clients(), source_name="clients.xlsx", imported_by="test")
    with pytest.raises(ValueError, match="reprise"):
        add(service, record, nom="Autre", principal_id=OTHER)
    with pytest.raises(StorageConflict):
        service.catalog.save(
            ADMIN, group="ATC", city="Angers", scope=record["scope"], identity=record["id"]
        )
    service.cancel(ADMIN, request["id"])
    service.store.save_clients(clients(), source_name="clients.xlsx", imported_by="test")


def test_scope_changes_alone_work_for_rag_and_dr_without_flow(service):
    for group, scope, new_scope in [
        ("RAG", {"rag_email": "rag@example.test"}, {"rag_email": "autre.rag@example.test"}),
        ("DR", {"ugs": ["20"]}, {"ugs": ["40"]}),
    ]:
        service.store.save_access_profile(
            UserAccessProfile(OID, "Jean", group.lower(), **scope), updated_by="test"
        )
        request = service.create(
            ADMIN, action="MODIFIER", group=group, principal_id=OID, scope=new_scope
        )
        assert request["resolved_by"] == "ADMIN"
        expected = {"ugs": ("40",)} if group == "DR" else new_scope
        assert business_scope(group, service.store.load_access_profile(OID)) == expected
    assert service.flow.calls == []


def test_only_admins_can_create_or_import_placeholders(service):
    atc = AuthenticatedUser("ATC", "entra", "atc", OID)
    with pytest.raises(PermissionError):
        service.catalog.import_users(atc, users(), clients())
    with pytest.raises(PermissionError):
        service.catalog.save(atc, group="ATC", city="Angers", scope={"atc_code": "164"})


def test_correcting_a_name_updates_the_reclaim_target_without_changing_selected_clients(service):
    record = entry(service)
    request = add(service, record, nom="Dupond")
    service.flow.verifier = lambda request: ResultatFlux(
        200, "INTROUVABLE", "Nom introuvable", None, {}, (), "test-run"
    )
    service.check(ADMIN, request["id"])
    service.flow.verifier = lambda request: ResultatFlux(
        200, "APPLIQUEE", "Confirmé", OID, {}, (), "test-run"
    )
    service.clarify(ADMIN, request["id"], nom="Dupont", prenom="Jean")
    after = service.store.load_clients()[0]
    assert after.loc[:1, "salesperson"].eq("Dupont Jean").all()
    assert after.loc[2, "salesperson"] == "Autre ATC"
    assert service.store.load_access_profile(OID).atc_name == "Dupont Jean"


def test_sqlite_rejects_oversized_documents_before_writing(service, monkeypatch):
    monkeypatch.setattr("opti_route.access_documents.MAX_DOCUMENT_BYTES", 32)
    with pytest.raises(StorageError, match="volumineux"):
        service.documents.write("access-scopes/too-big.json", {"name": "x" * 40}, None)
    assert service.documents.read("access-scopes/too-big.json") == (None, None)
