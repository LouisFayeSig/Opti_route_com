from __future__ import annotations

from io import BytesIO

import pandas as pd

from opti_route.data import (
    list_sheet_names,
    load_clients,
    standardize_clients,
    suggest_column_mapping,
)


def test_detects_real_world_french_column_variants() -> None:
    raw = pd.DataFrame(
        {
            "cli_code": ["C-001"],
            "rai_soc": ["Client Démo"],
            "nom": ["Camille Martin"],
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
    assert clients.loc[0, "full_address"] == "1 rue du Test, Bâtiment A, 14000, Caen, France"


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

