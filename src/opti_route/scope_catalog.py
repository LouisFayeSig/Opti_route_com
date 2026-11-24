"""Périmètres sans compte Entra et changements contrôlés d'affectation des clients."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import pandas as pd

from .access import AccessDeniedError, authorize_portfolio
from .access_documents import StorageConflict
from .auth import AuthenticatedUser
from .provisioning import (
    _DISPLAY_NAME_COLUMNS,
    _DR_UG_COLUMNS,
    _EMAIL_COLUMNS,
    _ENTRA_ROLE_COLUMNS,
    _OBJECT_ID_COLUMNS,
    _optional_column_name,
    _role,
    _split_ugs,
    _text,
)
from .storage import UserAccessProfile

PREFIX = "access-scopes/"
SCOPE_FIELDS = {
    "ATC": ("atc_code", "atc_email", "atc_name"),
    "RAG": ("rag_email", "agencies"),
    "DR": ("ugs",),
}


def business_scope(group, profile):
    if not isinstance(profile, dict):
        profile = asdict(profile)
    return {key: profile[key] for key in SCOPE_FIELDS[group] if profile.get(key)}


def scope_clients(clients, group, scope):
    if clients is None:
        return pd.DataFrame()
    profile = UserAccessProfile("scope-preview", "Périmètre", group.lower(), **scope).validated()
    try:
        return authorize_portfolio(
            clients, AuthenticatedUser("Aperçu", "preview", group.lower()), profile
        ).clients
    except AccessDeniedError:
        return clients.iloc[:0].copy()


def _normalized(series):
    return series.fillna("").astype(str).str.strip().str.casefold()


def unassigned_from_users(users, clients=None):
    """Conserve les lignes métier sans oid ; elles ne sont jamais des comptes autorisés."""
    if users.empty:
        return []
    role_column = _optional_column_name(users, _ENTRA_ROLE_COLUMNS)
    name_column = _optional_column_name(users, _DISPLAY_NAME_COLUMNS)
    if role_column is None or name_column is None:
        return []
    id_column = _optional_column_name(users, _OBJECT_ID_COLUMNS)
    email_column = _optional_column_name(users, _EMAIL_COLUMNS)
    code_column = _optional_column_name(users, ("code_atc", "atc_code", "code_commercial"))
    ug_column = _optional_column_name(users, _DR_UG_COLUMNS)
    records = {}
    for _, row in users.iterrows():
        if id_column and _text(row[id_column]):
            continue
        group = (_role(row[role_column]) or "").upper()
        name = " ".join(_text(row[name_column]).split())
        if group not in SCOPE_FIELDS or not name:
            continue
        email = _text(row[email_column]).casefold() if email_column else ""
        scope = {}
        if group == "ATC":
            scope = {"atc_code": _text(row[code_column]) if code_column else "", "atc_name": name}
            if email:
                scope["atc_email"] = email
            if clients is not None:
                matching = clients.loc[_normalized(clients["salesperson"]).eq(name.casefold())]
                if not matching.empty:
                    codes = matching["salesperson_code"].dropna().astype(str).unique()
                    if len(codes) == 1 and (
                        not scope["atc_code"] or codes[0].casefold() == name.casefold()
                    ):
                        scope["atc_code"] = codes[0]
                    # Un e-mail du classeur peut être une adresse de futur titulaire,
                    # absente du listing : le nom et le code restent alors les sélecteurs.
                    if email and not _normalized(matching["salesperson_email"]).eq(email).any():
                        scope.pop("atc_email", None)
            if scope.get("atc_code"):
                # Une ligne Utilisateurs décrit ce portefeuille précis, même si
                # plusieurs prête-noms partagent une adresse générique.
                scope.pop("atc_email", None)
        elif group == "RAG":
            scope = {"rag_email": email}
            if not email and clients is not None:
                matched = (
                    clients.loc[_normalized(clients["rag_name"]).eq(name.casefold()), "rag_email"]
                    .dropna()
                    .unique()
                )
                if len(matched) == 1:
                    scope["rag_email"] = str(matched[0]).strip().casefold()
        else:
            scope = {"ugs": list(_split_ugs(row[ug_column])) if ug_column else []}
        identity = json.dumps([group, name.casefold(), scope], sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(identity.encode()).hexdigest()
        records[key] = {
            "id": key,
            "group": group,
            "display_name": name,
            "scope": scope,
            "origin": "Utilisateurs",
        }
    return list(records.values())


def _fingerprint(frame, columns):
    values = (
        frame.reindex(columns=["client_id", *columns])
        .fillna("")
        .astype(str)
        .sort_values("client_id")
    )
    return hashlib.sha256(values.to_json(orient="values", force_ascii=False).encode()).hexdigest()


def prepare_assignment(store, group, source_scope, name, target_scope=None):
    """Journal compact avant/après, limité aux clients et colonnes sélectionnés."""
    clients, _ = store.load_clients()
    selected = scope_clients(clients, group, source_scope)
    if selected.empty:
        return None
    if clients["client_id"].astype(str).duplicated().any():
        raise ValueError("Les codes clients doivent être uniques pour réaffecter un portefeuille.")
    name = " ".join(name.split())
    if not name:
        raise ValueError("Le nom d'affectation est obligatoire.")
    target_scope = target_scope or {}
    segments = []
    if group == "ATC":
        values = {"salesperson": name}
        if target_scope.get("atc_code"):
            values["salesperson_code"] = target_scope["atc_code"]
        if "atc_email" in target_scope:
            values["salesperson_email"] = target_scope["atc_email"] or ""
        selections = [(selected, values)]
    elif group == "RAG":
        email = source_scope.get("rag_email", "").casefold()
        manager = selected.loc[_normalized(selected["rag_email"]).eq(email)] if email else selected
        personal = (
            selected.loc[_normalized(selected["salesperson_email"]).eq(email)]
            if email
            else selected.iloc[:0]
        )
        values = {"rag_name": name}
        own_values = {"salesperson": name}
        if target_scope.get("rag_email"):
            values["rag_email"] = own_values["salesperson_email"] = target_scope["rag_email"]
        selections = [(manager, values), (personal, own_values)]
    else:
        selections = [(selected, {"dr_name": name})]
    for frame, values in selections:
        if frame.empty:
            continue
        columns = list(values)
        before = frame.reindex(columns=["client_id", *columns]).fillna("").astype(str)
        after = before.copy()
        for key, value in values.items():
            after[key] = value
        groups = []
        originals = frame.reindex(columns=["client_id", *columns])
        for original, rows in originals.groupby(columns, dropna=False, sort=False):
            original = original if isinstance(original, tuple) else (original,)
            values_before = [None if pd.isna(value) else str(value) for value in original]
            groups.append(
                {
                    "ids": rows["client_id"].astype(str).tolist(),
                    "before": dict(zip(columns, values_before, strict=True)),
                }
            )
        segments.append(
            {
                "groups": groups,
                "after": values,
                "before_hash": _fingerprint(before, columns),
                "after_hash": _fingerprint(after, columns),
            }
        )
    return (
        {"group": group, "segments": segments, "client_count": len(selected)} if segments else None
    )


def assignment_ids(plan):
    return {
        identity
        for segment in (plan or {}).get("segments", [])
        for group in segment["groups"]
        for identity in group["ids"]
    }


def retarget_assignment(plan, name):
    """Une correction de nom change la cible, jamais les clients déjà sélectionnés."""
    for segment in (plan or {}).get("segments", []):
        for column in segment["after"]:
            if column in {"salesperson", "rag_name", "dr_name"}:
                segment["after"][column] = name
        rows = [
            {"client_id": identity, **group["before"], **segment["after"]}
            for group in segment["groups"]
            for identity in group["ids"]
        ]
        segment["after_hash"] = _fingerprint(pd.DataFrame(rows), list(segment["after"]))


def apply_assignment(store, plan, request_id, actor_id, *, reverse=False):
    if not plan:
        return
    clients, metadata = store.load_clients()
    if clients is None:
        raise StorageConflict("Le portefeuille à réaffecter n'est plus disponible.")
    result = clients.copy()
    changed = False
    for segment in plan["segments"]:
        ids = {identity for group in segment["groups"] for identity in group["ids"]}
        mask = result["client_id"].astype(str).isin(ids)
        if int(mask.sum()) != len(ids):
            raise StorageConflict(
                "La liste des clients a changé depuis la demande. Annulez puis recréez-la."
            )
        columns = list(segment["after"])
        current = _fingerprint(result.loc[mask], columns)
        source_hash, target_hash = (
            (segment["after_hash"], segment["before_hash"])
            if reverse
            else (segment["before_hash"], segment["after_hash"])
        )
        if current == target_hash:
            continue
        if current != source_hash:
            raise StorageConflict(
                "L'affectation des clients a changé depuis la demande. Aucun remplacement automatique n'est effectué."
            )
        if reverse:
            for group in segment["groups"]:
                rows = result["client_id"].astype(str).isin(group["ids"])
                for column, value in group["before"].items():
                    result.loc[rows, column] = value
        else:
            for column, value in segment["after"].items():
                result.loc[mask, column] = value
        changed = True
    if changed:
        store.save_clients(
            result,
            source_name=metadata.source_name,
            imported_by=actor_id,
            access_request_id=request_id,
        )


class ScopeCatalog:
    def __init__(self, store, authorize, clock=None):
        self.store, self.documents, self.authorize = store, store.access_documents, authorize
        self.clock = clock or (lambda: datetime.now(UTC))

    def get(self, identity):
        if not identity or not identity.isalnum():
            raise ValueError("Identifiant de périmètre invalide.")
        return self.documents.read(PREFIX + identity + ".json")[0]

    def entries(self):
        values = [self.documents.read(key)[0] for key in self.documents.keys(PREFIX)]
        return sorted(
            [value for value in values if value and not value.get("cancelled")],
            key=lambda value: (value["group"], value["display_name"].casefold()),
        )

    def put(self, record):
        key = PREFIX + record["id"] + ".json"
        _, etag = self.documents.read(key)
        self.documents.write(key, record, etag)
        return record

    def import_users(self, actor, users, clients=None):
        self.authorize(actor)
        records = unassigned_from_users(users, clients)
        with self.documents.transaction():
            self.authorize(actor)
            for record in records:
                if self.get(record["id"]) is None:
                    self.put({**record, "available": True, "updated_at": self.clock().isoformat()})
        return len(records)

    def save(self, actor, *, group, city, scope, identity=None):
        self.authorize(actor)
        if group not in SCOPE_FIELDS or not city.strip():
            raise ValueError("Le rôle et la ville du prête-nom sont obligatoires.")
        with self.documents.transaction():
            self.authorize(actor)
            previous = self.get(identity) if identity else None
            if identity:
                for key in self.documents.keys("access-requests/pending/"):
                    request, _ = self.documents.read(key)
                    if (
                        request
                        and request.get("catalog_id") == identity
                        and request.get("statut") in {"EN_ATTENTE", "A_PRECISER"}
                    ):
                        raise StorageConflict("Une reprise est en cours pour ce prête-nom.")
            if previous and (previous.get("assigned_to") or previous.get("waiting_for")):
                raise ValueError(
                    "Ce périmètre est déjà attribué ou concerné par un départ en cours."
                )
            record = {
                **(previous or {}),
                "id": identity or uuid4().hex,
                "group": group,
                "display_name": f"{group} {' '.join(city.split())}",
                "scope": scope,
                "origin": "Administration",
                "available": True,
                "updated_at": self.clock().isoformat(),
                "updated_by": actor.principal_id,
            }
            return self.put(record)
