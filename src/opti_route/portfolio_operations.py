"""Écritures coordonnées des périmètres et du listing, sans table de routage à la lecture."""

from dataclasses import asdict
from uuid import uuid4

from .access_documents import StorageConflict
from .scope_catalog import (
    alias_key,
    apply_assignment,
    assignment_ids,
    business_scope,
    catalog_clients,
    prepare_assignment,
    prepare_segments,
    scope_clients,
)
from .storage import UserAccessProfile

PENDING = "access-requests/portfolio-operations/pending/"


def profile_value(profile):
    if profile is None:
        return None
    return {
        k: list(v) if isinstance(v, tuple) else v
        for k, v in asdict(profile).items()
        if k not in {"updated_at", "updated_by"}
    }


def portfolios(clients):
    """Identités physiques des portefeuilles ATC (code, nom, e-mail)."""
    result = {}
    if clients is None:
        return result
    import hashlib

    for values, frame in clients.fillna("").groupby(
        ["salesperson_code", "salesperson", "salesperson_email"], sort=True
    ):
        identity = hashlib.sha256(repr(values).encode()).hexdigest()
        result[identity] = {
            "label": f"{values[0]} · {values[1]} · {len(frame)} clients",
            "clients": clients.loc[frame.index],
        }
    return result


class PortfolioOperations:
    def __init__(self, service):
        self.service, self.store, self.documents = service, service.store, service.documents

    def recover(self, actor):
        self.service._actor(actor)
        with self.documents.transaction():
            for key in self.documents.keys(PENDING):
                value, _ = self.documents.read(key)
                self._apply(value)

    def _apply(self, operation):
        target = operation.get("profile")
        if target:
            current = profile_value(self.store.load_access_profile(target["principal_id"]))
            if current not in (operation["before_profile"], target):
                raise StorageConflict(
                    "Le profil a changé pendant une écriture interrompue. Vérifiez le journal des périmètres."
                )
        catalogs = operation.get("catalogs", [])
        if operation.get("catalog"):
            catalogs = [
                *catalogs,
                {"before": operation["before_catalog"], "after": operation["catalog"]},
            ]
        for change in catalogs:
            current = self.service.catalog.get(change["after"]["id"])
            if current not in (change["before"], change["after"]):
                raise StorageConflict("Le prête-nom a changé pendant une écriture interrompue.")
        apply_assignment(self.store, operation["plan"], operation["id"], operation["actor"])
        if target:
            self.store.save_access_profile(
                UserAccessProfile(**target),
                updated_by=operation["actor"],
                access_request_id=operation["id"],
            )
        for change in catalogs:
            self.service.catalog.put(change["after"])
        key = PENDING + operation["id"] + ".json"
        archive = (
            "access-requests/portfolio-operations/done/"
            + operation["created_at"][:7]
            + "/"
            + operation["id"]
            + ".json"
        )
        previous, etag = self.documents.read(archive)
        if previous is None:
            self.documents.write(archive, {**operation, "completed_at": self.service._now()}, etag)
        _, etag = self.documents.read(key)
        if etag:
            self.documents.delete(key, etag)
        return operation

    def execute(
        self, actor, plan, *, profile=None, before_profile=None, catalog=None, before_catalog=None
    ):
        """Appelé sous le verrou partagé après validation de l'état courant."""
        self.service._actor(actor)
        ids = assignment_ids(plan)
        for request in self.service.pending():
            other = assignment_ids(request.get("portfolio_change")) | assignment_ids(
                request.get("vacancy", {}).get("change")
            )
            if (
                ids & other
                or (profile and request.get("objectId") == profile.principal_id)
                or (catalog and request.get("catalog_id") == catalog["id"])
            ):
                raise StorageConflict(
                    "Une demande AD concerne ce périmètre. Terminez-la avant de le modifier."
                )
        operation = {
            "id": uuid4().hex,
            "actor": actor.principal_id,
            "created_at": self.service._now(),
            "plan": plan,
            "profile": profile_value(profile),
            "before_profile": profile_value(before_profile),
            "catalog": catalog,
            "before_catalog": before_catalog,
        }
        operation["catalogs"] = [
            {
                "before": entry,
                "after": {
                    **entry,
                    "scope": business_scope(profile.role.upper(), profile),
                    "updated_at": self.service._now(),
                    "updated_by": actor.principal_id,
                },
            }
            for entry in self.service.catalog.entries()
            if profile
            and entry.get("assigned_to") == profile.principal_id
            and entry["group"] == profile.role.upper()
        ]
        self.documents.write(PENDING + operation["id"] + ".json", operation, None)
        return self._apply(operation)

    def update(
        self,
        actor,
        principal_id,
        *,
        expected_digest,
        expected_profile,
        atc_code=None,
        rag_email=None,
        rag_portfolios=None,
        ugs=None,
    ):
        self.service._actor(actor)
        with self.documents.transaction():
            self.recover(actor)
            clients, metadata = self.store.load_clients()
            current = self.store.load_access_profile(principal_id)
            if current is None or self.service.blocked(principal_id):
                raise ValueError("Sélectionnez une habilitation active existante.")
            if (
                clients is None
                or metadata.digest != expected_digest
                or profile_value(current) != expected_profile
            ):
                raise StorageConflict(
                    "Le portefeuille ou le profil a changé depuis l'ouverture du formulaire. Actualisez avant d'enregistrer."
                )
            target = profile_value(current)
            if current.role == "atc":
                if not atc_code or not atc_code.strip():
                    raise ValueError("Le code ATC est obligatoire.")
                selected = scope_clients(clients, "ATC", business_scope("ATC", current))
                if selected.empty:
                    raise ValueError(
                        "Aucun client retrouvé pour cet ATC ; corrigez d'abord son rattachement."
                    )
                target["atc_code"] = atc_code.strip()
                plan = prepare_assignment(
                    self.store,
                    "ATC",
                    {},
                    current.atc_name or current.display_name,
                    business_scope("ATC", target),
                    selected_clients=selected,
                )
                if (
                    rag_email is not None
                    and not selected["rag_email"]
                    .fillna("")
                    .str.casefold()
                    .eq(rag_email.casefold())
                    .all()
                ):
                    name = self._rag_name(rag_email, clients)
                    extra = prepare_segments(
                        [(selected, {"rag_name": name, "rag_email": rag_email})], group="ATC"
                    )
                    plan["segments"].extend(extra["segments"])
            elif current.role == "rag":
                choices = portfolios(clients)
                wanted = set(rag_portfolios or ())
                if wanted - choices.keys():
                    raise StorageConflict("La liste des portefeuilles a changé.")
                email = (current.rag_email or "").casefold()
                if not email:
                    raise ValueError("Le profil RAG doit avoir un e-mail.")
                selections = []
                for identity, choice in choices.items():
                    rows = choice["clients"]
                    owned = rows["rag_email"].fillna("").str.casefold().eq(email)
                    if identity in wanted:
                        selections.append(
                            (rows, {"rag_name": current.display_name, "rag_email": email})
                        )
                    else:
                        selections.append((rows.loc[owned], {"rag_name": "", "rag_email": ""}))
                plan = prepare_segments(selections, group="RAG")
            else:
                values = list(dict.fromkeys(ugs or ()))
                if not values or any(not str(ug).isdigit() or len(str(ug)) != 2 for ug in values):
                    raise ValueError("Sélectionnez au moins une UG à deux chiffres.")
                target["ugs"] = tuple(values)
                selections = []
                profiles = [
                    p
                    for p in self.store.list_access_profiles()
                    if p.role == "dr"
                    and p.principal_id != principal_id
                    and not self.service.blocked(p.principal_id)
                ]
                affected = clients.loc[clients["ug"].isin(set(current.ugs) | set(values))]
                for ug, rows in affected.groupby("ug"):
                    names = [p.display_name for p in profiles if ug in p.ugs]
                    if ug in values:
                        names.append(current.display_name)
                    # Les prête-noms DR conservent leur périmètre, même avec plusieurs contrôleurs.
                    names.extend(
                        e["display_name"]
                        for e in self.service.catalog.entries()
                        if e["group"] == "DR"
                        and e.get("available")
                        and ug in e["scope"].get("ugs", ())
                    )
                    selections.append(
                        (rows, {"dr_name": " / ".join(sorted(set(names))), "dr_email": ""})
                    )
                plan = prepare_segments(selections, group="DR")
            return self.execute(
                actor, plan, profile=UserAccessProfile(**target).validated(), before_profile=current
            )

    def _rag_name(self, email, clients):
        if not email:
            return ""
        for profile in self.store.list_access_profiles():
            if (
                profile.role == "rag"
                and profile.rag_email == email
                and not self.service.blocked(profile.principal_id)
            ):
                return profile.display_name
        names = (
            clients.loc[clients["rag_email"].fillna("").str.casefold().eq(email), "rag_name"]
            .dropna()
            .astype(str)
        )
        names = names.loc[names.str.strip().ne("")].unique()
        if len(names) == 1:
            return str(names[0])
        if clients["rag_email"].fillna("").str.casefold().eq(email).any():
            return " / ".join(sorted(names)) or email
        raise ValueError("Sélectionnez un RAG connu.")

    def repair_catalog(self, actor):
        """Réconcilie les anciens catalogues sans élargir un code partagé à d'autres ATC."""
        self.service._actor(actor)
        count = 0
        with self.documents.transaction():
            self.recover(actor)
            entries = self.service.catalog.entries()
            import hashlib
            import json

            clients, metadata = self.store.load_clients()
            if clients is None:
                return 0
            signature = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
            marker_key = "access-requests/catalog-listing-repair.json"
            marker, marker_etag = self.documents.read(marker_key)
            if marker == {"version": 1, "digest": metadata.digest, "catalog": signature}:
                return 0
            for entry in entries:
                if entry["group"] != "ATC" or entry.get("waiting_for"):
                    continue
                if any(
                    e["id"] != entry["id"]
                    and alias_key(e["display_name"]) == alias_key(entry["display_name"])
                    for e in entries
                ):
                    continue  # Ambigu : ne jamais deviner le code cible.
                owner = (
                    self.store.load_access_profile(entry["assigned_to"])
                    if entry.get("assigned_to")
                    else None
                )
                if entry.get("assigned_to") and (
                    not owner or owner.role != "atc" or self.service.blocked(owner.principal_id)
                ):
                    continue
                clients, _ = self.store.load_clients()
                matched = catalog_clients(clients, entry, owner)
                if matched.empty:
                    continue
                scope = business_scope("ATC", owner) if owner else entry["scope"]
                name = (owner.atc_name or owner.display_name) if owner else entry["display_name"]
                plan = prepare_assignment(
                    self.store, "ATC", {}, name, scope, selected_clients=matched
                )
                if all(s["before_hash"] == s["after_hash"] for s in plan["segments"]):
                    continue
                if any(
                    assignment_ids(plan)
                    & (
                        assignment_ids(r.get("portfolio_change"))
                        | assignment_ids(r.get("vacancy", {}).get("change"))
                    )
                    for r in self.service.pending()
                ):
                    continue
                updated = {
                    **entry,
                    "source_scope": entry.get("source_scope", entry["scope"]),
                    "scope": {**entry["scope"], "atc_name": name},
                    "listing_repaired_at": self.service._now(),
                }
                self.execute(actor, plan, catalog=updated, before_catalog=entry)
                count += plan["client_count"]
            _, metadata = self.store.load_clients()
            signature = hashlib.sha256(
                json.dumps(self.service.catalog.entries(), sort_keys=True).encode()
            ).hexdigest()
            self.documents.write(
                marker_key,
                {"version": 1, "digest": metadata.digest, "catalog": signature},
                marker_etag,
            )
        return count
