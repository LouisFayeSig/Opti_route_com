from __future__ import annotations

from io import BytesIO

import pandas as pd
import pytest

from opti_route.data import (
    ClientDataError,
    list_sheet_names,
    load_clients,
    standardize_clients,
    suggest_column_mapping,
    validate_uploaded_file,
)


def test_detects_real_world_french_column_variants() -> None:
    raw = pd.DataFrame(
        {
            "cli_code": ["C-001"],
            "rai_soc": ["Client Démo"],
            "nom": ["Camille Martin"],
            "Agence": ["Caen"],
            "Adresse agence": ["8 rue Ampère, 14120 Mondeville"],
            "adr1": ["1 rue du Test"],
            "adr2": ["Bâtiment A"],
            "cp": ["14000"],
            "ville": ["Caen"],
        }
    )

    mapping = suggest_column_mapping(raw.columns)
    clients = standardize_clients(raw)

    assert mapping["client_name"] == "rai_soc"
    assert mapping["salesperson"] == "nom"
    assert clients.loc[0, "client_id"] == "C-001"
    assert clients.loc[0, "salesperson"] == "Camille Martin"
    assert clients.loc[0, "agency"] == "Caen"
    assert clients.loc[0, "agency_address"] == "8 rue Ampère, 14120 Mondeville"
    assert clients.loc[0, "full_address"] == "1 rue du Test, Bâtiment A, 14000, Caen, France"


def test_detects_atc_code_and_falls_back_to_salesperson_for_legacy_files() -> None:
    with_code = pd.DataFrame(
        {
            "Code client": ["C1"],
            "Client": ["Alpha"],
            "Commercial": ["Alice"],
            "Code ATC": ["ATC-001"],
            "Adresse": ["1 rue du Test, Caen"],
        }
    )
    legacy = with_code.drop(columns=["Code ATC"])

    assert standardize_clients(with_code).loc[0, "salesperson_code"] == "ATC-001"
    assert standardize_clients(legacy).loc[0, "salesperson_code"] == "Alice"


def test_recognizes_final_listing_contract_and_propagates_manager_scope() -> None:
    raw = pd.DataFrame(
        {
            "rai_soc": ["Alpha", "Beta", "Gamma"],
            "RUE": ["1 rue A", "2 rue B", "3 rue C"],
            "cp": ["14000", "14000", "76000"],
            "ville": ["Caen", "Caen", "Rouen"],
            "Nom": ["Alice", "Alice", "Bob"],
            "Code Agence": ["14", "14", "76"],
            "code ATC": ["01", "01", "01"],
            "N+1": [pd.NA, "Directrice Alice", "Directeur Bob"],
            "Email Nom (E)": ["alice@example.test", pd.NA, "bob@example.test"],
            "Email N+1 (H)": [pd.NA, "manager.a@example.test", "manager.b@example.test"],
        }
    )

    mapping = suggest_column_mapping(raw.columns)
    clients = standardize_clients(raw)

    assert mapping == {
        "client_name": "rai_soc",
        "salesperson_code": "code ATC",
        "salesperson": "Nom",
        "salesperson_email": "Email Nom (E)",
        "director_name": "N+1",
        "director_email": "Email N+1 (H)",
        "agency": "Code Agence",
        "address": "RUE",
        "postal_code": "cp",
        "city": "ville",
    }
    assert clients.loc[0, "full_address"] == "1 rue A, 14000, Caen, France"
    assert clients.loc[0, "salesperson_email"] == "alice@example.test"
    assert clients.loc[0, "director_name"] == "Directrice Alice"
    assert clients.loc[0, "director_email"] == "manager.a@example.test"
    assert clients.loc[2, "director_email"] == "manager.b@example.test"


def test_manual_mapping_accepts_unknown_column_names() -> None:
    raw = pd.DataFrame(
        {
            "Société visitée": ["Alpha"],
            "Gestionnaire": ["Morgan"],
            "Localisation": ["10 avenue de Paris"],
            "Municipalité": ["Rouen"],
        }
    )
    clients = standardize_clients(
        raw,
        {
            "client_name": "Société visitée",
            "salesperson": "Gestionnaire",
            "address": "Localisation",
            "city": "Municipalité",
        },
    )
    assert clients.loc[0, "client_name"] == "Alpha"
    assert clients.loc[0, "salesperson"] == "Morgan"
    assert clients.loc[0, "city"] == "Rouen"


def test_address_only_file_does_not_require_client_or_salesperson() -> None:
    raw = pd.DataFrame(
        {
            "Adresse complète": ["8 rue Ampère"],
            "Code postal": ["14120"],
            "Ville": ["Mondeville"],
        }
    )

    clients = standardize_clients(raw)

    assert clients.loc[0, "client_id"] == "ADRESSE-1"
    assert clients.loc[0, "client_name"] == "8 rue Ampère, 14120, Mondeville, France"
    assert clients.loc[0, "salesperson"] == "Tous"


def test_reads_workbook_sheet_and_custom_header_row() -> None:
    workbook = BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame([["Rapport clients"], ["Client"], ["Alpha"]]).to_excel(
            writer, sheet_name="Portefeuille", index=False, header=False
        )
        pd.DataFrame({"Client": ["Beta"]}).to_excel(writer, sheet_name="Autre", index=False)
    payload = workbook.getvalue()

    assert list_sheet_names(BytesIO(payload), filename="clients.xlsx") == ["Portefeuille", "Autre"]
    clients = load_clients(
        BytesIO(payload),
        filename="clients.xlsx",
        sheet_name="Portefeuille",
        header_row=1,
    )
    assert clients["client_name"].tolist() == ["Alpha"]


def test_upload_validation_rejects_fake_office_archives() -> None:
    with pytest.raises(ClientDataError, match="invalide ou corrompu"):
        validate_uploaded_file(b"not-an-office-file", "clients.xlsx")
