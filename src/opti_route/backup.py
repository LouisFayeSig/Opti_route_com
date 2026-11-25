"""Copies administrateur de l'état courant ; aucun secret de connexion n'est exporté."""

from __future__ import annotations

import io
import json
import time
from dataclasses import asdict
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile
from zoneinfo import ZoneInfo

import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell


def capture_backup(service, actor):
    service._actor(actor)
    documents = service.documents
    with documents.transaction():
        renewed_at = time.monotonic()
        service._actor(actor)
        service.operations.recover(actor)
        service.pending()  # Termine les écritures déjà autorisées avant la capture.
        service._actor(actor)
        clients, metadata = service.store.load_clients()
        stored_profiles = [asdict(profile) for profile in service.store.list_access_profiles()]
        state = {}
        for prefix in ("access-state/", "access-scopes/", "access-requests/"):
            for key in documents.keys(prefix):
                if time.monotonic() - renewed_at >= 20 and hasattr(documents, "_renew"):
                    documents._renew()
                    renewed_at = time.monotonic()
                value, _ = documents.read(key)
                if value is not None:
                    state[key] = value
        states = {
            value["principal_id"]: value
            for key, value in state.items()
            if key.startswith("access-state/") and value.get("principal_id")
        }
        profiles = {
            profile["principal_id"]: {
                **profile,
                "enabled": states.get(profile["principal_id"], {}).get("enabled", True) is True,
            }
            for profile in stored_profiles
        }
        for principal_id, access in states.items():
            if access.get("role") == "admin":
                profiles[principal_id] = {
                    **access.get("admin_profile", {}),
                    "principal_id": principal_id,
                    "role": "admin",
                    "enabled": access.get("enabled", True) is True,
                }
        generated_at = service.clock().astimezone(ZoneInfo("Europe/Paris"))
        return {
            "format": "opti-route.backup.v1",
            "generated_at": generated_at.isoformat(),
            "exported_by": actor.principal_id,
            "portfolio_metadata": asdict(metadata) if metadata else None,
            "clients": json.loads(clients.to_json(orient="records", force_ascii=False))
            if clients is not None
            else [],
            "profiles": list(profiles.values()),
            "stored_profiles": stored_profiles,
            "route_configuration": asdict(service.store.load_route_configuration()),
            "documents": state,
        }


def _scope_row(profile):
    return {
        "Role Entra": profile["role"].upper(),
        "Nom affiche": profile.get("display_name", ""),
        "Object ID": profile.get("principal_id", ""),
        "UPN ou e-mail": profile.get("atc_email") or profile.get("rag_email") or "",
        "Code ATC": profile.get("atc_code") or "",
        "Commercial ATC": profile.get("atc_name") or "",
        "E-mail ATC": profile.get("atc_email") or "",
        "E-mail RAG": profile.get("rag_email") or "",
        "Code Region": "-".join(profile.get("ugs") or ()),
        "Agences": ";".join(profile.get("agencies") or ()),
        "Acces actif": profile.get("enabled", True),
    }


def _append_sheet(book, name, frame):
    sheet = book.create_sheet(name)
    sheet.freeze_panes = "A2"
    sheet.append(list(frame.columns))
    for row in frame.itertuples(index=False, name=None):
        values = []
        for value in row:
            if value is None or (not isinstance(value, str) and pd.isna(value)):
                values.append(None)
            elif isinstance(value, str):
                cell = WriteOnlyCell(sheet, value=value)
                cell.data_type = "s"  # Conserve les valeurs exactes, sans formule Excel.
                values.append(cell)
            else:
                values.append(value)
        sheet.append(values)


def backup_workbook_bytes(snapshot):
    book = Workbook(write_only=True)
    _append_sheet(book, "Listing client", pd.DataFrame(snapshot["clients"]))
    profiles = snapshot["profiles"]
    active = [_scope_row(profile) for profile in profiles if profile.get("enabled", True)]
    placeholders = []
    requests = []
    for key, value in snapshot["documents"].items():
        if key.startswith("access-scopes/"):
            row = _scope_row(
                {
                    "role": value["group"].lower(),
                    "display_name": value["display_name"],
                    **value["scope"],
                }
            )
            row.update(
                {
                    "Id perimetre": value["id"],
                    "Etat": "Annulé"
                    if value.get("cancelled")
                    else "Attribué"
                    if value.get("assigned_to")
                    else "Départ en cours"
                    if value.get("waiting_for")
                    else "Disponible",
                    "Object ID repreneur": value.get("assigned_to") or "",
                }
            )
            placeholders.append(row)
            if not value.get("cancelled") and not value.get("assigned_to"):
                active.append({**row, "Acces actif": False})
        elif key.startswith(("access-requests/pending/", "access-requests/done/")):
            requests.append(
                {
                    "Demande": value["id"],
                    "Action": value["action"],
                    "Groupe": value["groupe"],
                    "Nom": value["nom"],
                    "Prenom": value["prenom"],
                    "Object ID": value.get("objectId") or "",
                    "Statut": value["statut"],
                    "Perimetre": value.get("code", ""),
                    "Creee le": value["created_at"],
                    "Cloturee le": value.get("resolved_at") or "",
                }
            )
    user_columns = list(_scope_row({"role": "atc"}))
    _append_sheet(book, "Utilisateurs", pd.DataFrame(active).reindex(columns=user_columns))
    _append_sheet(
        book,
        "Habilitations",
        pd.DataFrame([_scope_row(profile) for profile in profiles]).reindex(columns=user_columns),
    )
    _append_sheet(
        book,
        "Prete-noms",
        pd.DataFrame(placeholders).reindex(
            columns=[*user_columns, "Id perimetre", "Etat", "Object ID repreneur"]
        ),
    )
    _append_sheet(book, "Demandes AD", pd.DataFrame(requests))
    info = [
        {"Information": "Date de la copie", "Valeur": snapshot["generated_at"]},
        {
            "Information": "Contenu",
            "Valeur": "Données normalisées courantes, habilitations, prête-noms et demandes AD. Aucune clé de connexion.",
        },
        {
            "Information": "Réimport des clients",
            "Valeur": "Sélectionner Listing client. Pour conserver les habilitations existantes, choisir Ne pas modifier les habilitations.",
        },
        {
            "Information": "Réimport des comptes",
            "Valeur": "Le provisionnement Entra depuis Utilisateurs nécessite un e-mail/UPN pour ATC, RAG et DR. Les profils par code seul restent conservés dans Habilitations et dans la sauvegarde JSON.",
        },
        {
            "Information": "Sauvegarde complète",
            "Valeur": "L'archive ZIP contient aussi etat_application.json : profils, désactivations, réglages, catalogue et journaux des demandes. Le JSON n'est pas restauré par l'import Excel classique.",
        },
        {
            "Information": "Classeur original",
            "Valeur": "Cette copie reconstruit les données de l'application ; elle ne conserve pas les feuilles métier non importées ou la mise en forme du classeur original.",
        },
    ]
    _append_sheet(book, "Lire-moi", pd.DataFrame(info))
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def backup_archive_bytes(snapshot, workbook):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("portefeuille.xlsx", workbook, compress_type=ZIP_STORED)
        archive.writestr(
            "etat_application.json",
            json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            compress_type=ZIP_DEFLATED,
        )
    return output.getvalue()
