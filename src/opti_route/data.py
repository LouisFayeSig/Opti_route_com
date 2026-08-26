from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path
from typing import BinaryIO

import pandas as pd


class ClientDataError(ValueError):
    """Le fichier clients ne peut pas être converti vers le schéma attendu."""


ALIASES: dict[str, tuple[str, ...]] = {
    "client_id": (
        "client_id",
        "id_client",
        "code_client",
        "numero_client",
        "num_client",
        "cli_code",
        "compte",
        "code_compte",
    ),
    "client_name": (
        "client_name",
        "nom_client",
        "client",
        "raison_sociale",
        "rai_soc",
        "nom_compte",
        "enseigne",
        "societe",
        "nom",
    ),
    "salesperson": (
        "salesperson",
        "commercial",
        "nom_commercial",
        "commercial_nom",
        "vendeur",
        "responsable_commercial",
        "charge_affaires",
        "account_manager",
        "representant",
        "nom",
    ),
    "address": (
        "address",
        "adresse",
        "adresse_1",
        "adresse1",
        "adr1",
        "rue",
        "voie",
        "adr1_compte",
    ),
    "address_2": (
        "address_2",
        "adresse_2",
        "adresse2",
        "adr2",
        "complement_adresse",
        "complement_adresse_1",
    ),
    "address_3": (
        "address_3",
        "adresse_3",
        "adresse3",
        "adr3",
        "complement_adresse_2",
    ),
    "postal_code": (
        "postal_code",
        "code_postal",
        "cp",
        "cd_postal",
        "cd_postal_compte",
        "zip",
    ),
    "city": ("city", "ville", "commune", "localite", "ville_compte"),
    "country": ("country", "pays", "pays_compte"),
    "latitude": ("latitude", "lat", "gps_latitude", "y"),
    "longitude": ("longitude", "lon", "lng", "gps_longitude", "x"),
}


def normalize_column_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _excel_engine(suffix: str) -> str:
    engines = {
        ".xls": "xlrd",
        ".xlsx": "openpyxl",
        ".xlsm": "openpyxl",
        ".xlsb": "pyxlsb",
    }
    try:
        return engines[suffix]
    except KeyError as exc:
        raise ClientDataError(
            "Format non pris en charge. Utilisez CSV, XLS, XLSX, XLSM ou XLSB."
        ) from exc


def read_tabular(
    source: Path | BinaryIO | io.BytesIO,
    filename: str | None = None,
    sheet_name: str | int = 0,
    header_row: int = 0,
) -> pd.DataFrame:
    suffix = Path(filename or getattr(source, "name", "")).suffix.casefold()
    if suffix == ".csv":
        return pd.read_csv(source, sep=None, engine="python", dtype=str, header=header_row)
    return pd.read_excel(
        source,
        dtype=str,
        engine=_excel_engine(suffix),
        sheet_name=sheet_name,
        header=header_row,
    )


def list_sheet_names(source: Path | BinaryIO | io.BytesIO, filename: str | None = None) -> list[str]:
    suffix = Path(filename or getattr(source, "name", "")).suffix.casefold()
    if suffix == ".csv":
        return ["Données"]
    excel_file = pd.ExcelFile(source, engine=_excel_engine(suffix))
    return excel_file.sheet_names


def suggest_column_mapping(columns: pd.Index) -> dict[str, str]:
    normalized = {normalize_column_name(column): str(column) for column in columns}
    mapping: dict[str, str] = {}
    claimed: set[str] = set()
    for target, aliases in ALIASES.items():
        for alias in aliases:
            source = normalized.get(alias)
            if source is not None and source not in claimed:
                mapping[target] = source
                claimed.add(source)
                break
    return mapping


def standardize_clients(
    raw: pd.DataFrame, column_mapping: dict[str, str | None] | None = None
) -> pd.DataFrame:
    raw = raw.dropna(how="all").copy()
    if raw.empty:
        raise ClientDataError("Le fichier clients ne contient aucune ligne exploitable.")

    mapping = suggest_column_mapping(raw.columns)
    if column_mapping:
        valid_columns = {str(column) for column in raw.columns}
        mapping.update(
            {
                target: source
                for target, source in column_mapping.items()
                if source is not None and source in valid_columns
            }
        )
    if "client_name" not in mapping and "client_id" not in mapping:
        available = ", ".join(map(str, raw.columns))
        raise ClientDataError(
            "Impossible d'identifier la colonne du nom ou du code client. "
            f"Colonnes détectées : {available}"
        )

    clients = pd.DataFrame(index=raw.index)
    for target in ALIASES:
        source_column = mapping.get(target)
        clients[target] = raw[source_column] if source_column else pd.NA

    if clients["client_name"].isna().all():
        clients["client_name"] = clients["client_id"]
    clients["client_name"] = clients["client_name"].fillna(clients["client_id"])
    clients["client_id"] = clients["client_id"].fillna(
        pd.Series([f"CLIENT-{position + 1}" for position in range(len(clients))], index=clients.index)
    )
    clients["salesperson"] = clients["salesperson"].fillna("Tous")
    clients["country"] = clients["country"].fillna("France")

    text_columns = [
        "client_id",
        "client_name",
        "salesperson",
        "address",
        "postal_code",
        "city",
        "country",
        "address_2",
        "address_3",
    ]
    for column in text_columns:
        clients[column] = clients[column].astype("string").str.strip()

    # Excel transforme parfois les codes postaux en nombres décimaux.
    clients["postal_code"] = clients["postal_code"].str.replace(r"\.0$", "", regex=True)
    clients["latitude"] = pd.to_numeric(
        clients["latitude"].astype("string").str.replace(",", ".", regex=False), errors="coerce"
    )
    clients["longitude"] = pd.to_numeric(
        clients["longitude"].astype("string").str.replace(",", ".", regex=False), errors="coerce"
    )
    clients["full_address"] = clients[
        ["address", "address_2", "address_3", "postal_code", "city", "country"]
    ].apply(
        lambda row: ", ".join(str(value) for value in row if pd.notna(value) and str(value).strip()),
        axis=1,
    )
    clients = clients[clients["client_name"].notna()].reset_index(drop=True)
    if clients.empty:
        raise ClientDataError("Aucun client nommé n'a été trouvé dans le fichier.")
    return clients


def load_clients(
    source: Path | BinaryIO | io.BytesIO,
    filename: str | None = None,
    sheet_name: str | int = 0,
    header_row: int = 0,
    column_mapping: dict[str, str | None] | None = None,
) -> pd.DataFrame:
    raw = read_tabular(
        source, filename=filename, sheet_name=sheet_name, header_row=header_row
    )
    return standardize_clients(raw, column_mapping=column_mapping)


def discover_client_files(project_root: Path) -> list[Path]:
    supported = {".csv", ".xls", ".xlsx", ".xlsm", ".xlsb"}
    data_directory = project_root / "data"
    if not data_directory.exists():
        return []
    return sorted(
        path for path in data_directory.iterdir() if path.is_file() and path.suffix.casefold() in supported
    )
