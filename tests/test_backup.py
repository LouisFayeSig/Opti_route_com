import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pandas as pd
import pytest
from openpyxl import load_workbook

from opti_route.access_requests import AccessRequests
from opti_route.auth import AuthenticatedUser
from opti_route.backup import backup_archive_bytes, backup_workbook_bytes, capture_backup
from opti_route.data import read_tabular, standardize_clients
from opti_route.storage import AppStore, UserAccessProfile

OID = "11111111-1111-4111-8111-111111111111"
ADMIN = AuthenticatedUser("Admin", "entra", "admin", "33333333-3333-4333-8333-333333333333")


@pytest.fixture
def service():
    path = Path(".cache") / f"backup-test-{uuid4().hex}.sqlite3"
    store = AppStore(path)
    portfolio = standardize_clients(
        pd.DataFrame(
            {
                "client_id": ["0001", "0002"],
                "client_name": ["Alpha", "=2+2"],
                "salesperson": ["Jean", "Autre"],
                "salesperson_code": ["00164", "999"],
                "address": ["1 rue du Test"] * 2,
                "postal_code": ["14000"] * 2,
                "city": ["Caen"] * 2,
            }
        )
    )
    store.save_clients(portfolio, source_name="clients.xlsx", imported_by="test")
    store.save_access_profile(
        UserAccessProfile(OID, "Jean", "atc", atc_code="00164", atc_name="Jean"), updated_by="test"
    )
    calls = []
    value = AccessRequests(
        store, SimpleNamespace(verifier=lambda request: calls.append(request), calls=calls)
    )
    yield value
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_backup_exports_current_assignments_placeholders_disabled_access_and_pending_request(
    service,
):
    request = service.create(
        ADMIN, action="SUPPRIMER", group="ATC", principal_id=OID, vacant_city="Angers"
    )
    snapshot = capture_backup(service, ADMIN)
    assert snapshot["clients"][0]["salesperson"] == "ATC Angers"
    assert snapshot["profiles"][0]["enabled"] is False
    assert (
        snapshot["documents"][f"access-requests/pending/{request['id']}.json"]["statut"]
        == "EN_ATTENTE"
    )
    assert any(
        value.get("enabled") is False
        for key, value in snapshot["documents"].items()
        if key.startswith("access-state/")
    )
    assert service.flow.calls == []
    data = backup_workbook_bytes(snapshot)
    workbook = load_workbook(io.BytesIO(data))
    assert workbook.sheetnames == [
        "Listing client",
        "Utilisateurs",
        "Habilitations",
        "Prete-noms",
        "Demandes AD",
        "Lire-moi",
    ]
    listing = read_tabular(io.BytesIO(data), filename="copie.xlsx", sheet_name="Listing client")
    assert listing.loc[0, "salesperson"] == "ATC Angers"
    assert listing.loc[0, "salesperson_code"] == "00164"
    assert listing.loc[0, "client_id"] == "0001"
    users = read_tabular(io.BytesIO(data), filename="copie.xlsx", sheet_name="Utilisateurs")
    assert users["Nom affiche"].tolist() == ["ATC Angers"]
    assert users["Object ID"].isna().all()
    profiles = read_tabular(io.BytesIO(data), filename="copie.xlsx", sheet_name="Habilitations")
    assert profiles.loc[0, "Object ID"] == OID
    assert profiles.loc[0, "Acces actif"] == "False"


def test_zip_contains_excel_and_full_business_state_without_connection_secrets(
    service, monkeypatch
):
    monkeypatch.setenv("FLOW_LECTURE_AD_URL", "https://private.example.test/SECRET_URL")
    monkeypatch.setenv("AUTH_PASSWORD", "SECRET_PASSWORD")
    snapshot = capture_backup(service, ADMIN)
    workbook = backup_workbook_bytes(snapshot)
    with ZipFile(io.BytesIO(backup_archive_bytes(snapshot, workbook))) as archive:
        assert archive.namelist() == ["portefeuille.xlsx", "etat_application.json"]
        assert archive.read("portefeuille.xlsx") == workbook
        state = json.loads(archive.read("etat_application.json"))
        assert state["format"] == "opti-route.backup.v1"
        assert state["profiles"][0]["principal_id"] == OID
        assert state["route_configuration"]["max_visits"] == 10
        assert state["clients"][0]["client_id"] == "0001"
        assert "SECRET_URL" not in str(state)
        assert "SECRET_PASSWORD" not in str(state)


def test_export_keeps_formula_like_values_as_exact_literal_text_and_can_reimport_clients(service):
    snapshot = capture_backup(service, ADMIN)
    data = backup_workbook_bytes(snapshot)
    sheet = load_workbook(io.BytesIO(data))["Listing client"]
    assert sheet["B3"].value == "=2+2"
    assert sheet["B3"].data_type == "s"
    rows = standardize_clients(
        read_tabular(io.BytesIO(data), filename="copie.xlsx", sheet_name="Listing client")
    )
    assert rows["client_id"].tolist() == ["0001", "0002"]
    assert rows["salesperson_code"].tolist() == ["00164", "999"]
    assert rows["salesperson"].tolist() == ["Jean", "Autre"]


def test_only_an_enabled_admin_can_capture_a_backup(service):
    with pytest.raises(PermissionError):
        capture_backup(service, AuthenticatedUser("Jean", "entra", "atc", OID))
    service._state(ADMIN.principal_id, {"enabled": False})
    with pytest.raises(PermissionError):
        capture_backup(service, ADMIN)
