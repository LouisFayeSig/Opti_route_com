"""Cycle des demandes, preuves AD/connexion et désactivation locale des accès."""

from __future__ import annotations

import copy
import hashlib
import re
import time
import unicodedata
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .access_documents import StorageConflict
from .flow_client import GROUPS, AdUnavailable, RequeteFluxInvalide, object_id
from .scope_catalog import (
    ScopeCatalog,
    apply_assignment,
    assignment_ids,
    business_scope,
    catalog_clients,
    prepare_assignment,
    retarget_assignment,
)
from .storage import StorageError, UserAccessProfile

OPEN = {"EN_ATTENTE", "A_PRECISER"}
PENDING = "access-requests/pending/"
STATES = "access-state/"


def _identity(value):
    return hashlib.sha256(value.strip().casefold().encode("utf-8")).hexdigest()


def _normalized(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c)).split())


def _admin(actor):
    if not actor.is_admin:
        raise PermissionError("Cette opération est réservée aux administrateurs.")


class AccessRequests:
    def __init__(self, store, flow=None, *, clock=None, timer=time.monotonic):
        self.store = store
        self.documents = store.access_documents
        self.flow = flow
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timer = timer

    def _now(self):
        return self.clock().isoformat()

    @property
    def catalog(self):
        return ScopeCatalog(self.store, self._actor, self.clock, service=self)

    @property
    def operations(self):
        from .portfolio_operations import PortfolioOperations
        return PortfolioOperations(self)

    def state(self, principal_id):
        principal_id = principal_id.strip().casefold()
        value, _ = self.documents.read(STATES + _identity(principal_id) + ".json")
        if value and (value.get("principal_id") != principal_id or type(value.get("enabled", True)) is not bool):
            raise StorageError("État d'habilitation incohérent.")
        return value or {}

    def _actor(self, actor):
        _admin(actor)
        self.ensure_session(actor)

    def ensure_session(self, user):
        state = self.state(user.principal_id)
        if state.get("enabled", True) is False:
            raise PermissionError("Votre accès Opti Route a été désactivé. Contactez un administrateur.")
        if user.method == "entra" and state.get("role") and state["role"] != user.role:
            raise PermissionError("Votre groupe a changé. Reconnectez-vous à Microsoft pour actualiser vos droits.")

    def _state(self, principal_id, value):
        principal_id = principal_id.strip().casefold()
        key = STATES + _identity(principal_id) + ".json"
        _, etag = self.documents.read(key)
        self.documents.write(key, {**value, "principal_id": principal_id}, etag)

    def blocked(self, principal_id):
        return self.state(principal_id).get("enabled", True) is False

    def profile(self, principal_id):
        state = self.state(principal_id)
        profile = self.store.load_access_profile(principal_id)
        value = asdict(profile) if profile is not None else None
        if state.get("role") == "admin":
            value = dict(state.get("admin_profile") or {"principal_id": principal_id, "role": "admin"})
        if value is not None:
            value["enabled"] = state.get("enabled", True)
        return value

    def profiles(self):
        values = {profile.principal_id: self.profile(profile.principal_id) for profile in self.store.list_access_profiles()}
        for key in self.documents.keys(STATES):
            state, _ = self.documents.read(key)
            if state and state.get("role") == "admin":
                values[state["principal_id"]] = self.profile(state["principal_id"])
        return [value for value in values.values() if value]

    def get(self, request_id):
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("Identifiant de demande invalide.")
        value, _ = self.documents.read(PENDING + request_id + ".json")
        return value

    def _save(self, request):
        key = PENDING + request["id"] + ".json"
        existing, etag = self.documents.read(key)
        if existing is None and request["revision"] != 0:
            raise StorageConflict("La demande n'est plus en cours.")
        if existing is not None and existing["revision"] != request["revision"]:
            raise StorageConflict("La demande a changé pendant le contrôle.")
        request["revision"] += 1
        self.documents.write(key, request, etag)

    def _event(self, request, event, **details):
        request["history"].append({"date": self._now(), "event": event, **details})

    def _pending(self):
        # Une création Azure peut s'interrompre après la désactivation et avant
        # l'écriture de la demande. Le journal embarqué permet de la matérialiser.
        for key in self.documents.keys(STATES):
            state, _ = self.documents.read(key)
            recovery = (state or {}).get("_pending_request")
            if recovery and self.get(recovery["id"]) is None:
                self._save(recovery)
        return [value for key in self.documents.keys(PENDING) if (value := self.documents.read(key)[0]) is not None]

    def pending(self):
        with self.documents.transaction():
            requests = self._pending()
            for request in requests:
                if request.get("cancelling"):
                    self._cancel(request)
                elif request.get("application"):
                    self._finish(request, request["application"]["proof"])
                elif request["statut"] in OPEN and request.get("vacancy"):
                    self._maintain_vacancy(request)
                elif request["statut"] not in OPEN:
                    self._archive(request)
            return sorted([request for request in self._pending() if request["statut"] in OPEN], key=lambda request: request["created_at"])

    def recent(self):
        month_index = self.clock().year * 12 + self.clock().month - 1
        months = {f"{(month_index - offset) // 12:04d}-{(month_index - offset) % 12 + 1:02d}" for offset in range(3)}
        values = {}
        for month in months:
            for key in self.documents.keys(f"access-requests/done/{month}/"):
                request, _ = self.documents.read(key)
                if request:
                    values[request["id"]] = request
        return sorted(values.values(), key=lambda value: value["resolved_at"], reverse=True)

    def _scope(self, group, scope):
        scope = dict(scope or {})
        if group == "ATC":
            code = re.sub(r"\s+", "", str(scope.get("atc_code") or ""))
            if not code:
                raise ValueError("Le code ATC est obligatoire.")
            return {"atc_code": code, "atc_email": str(scope.get("atc_email") or "").strip().casefold() or None,
                    "atc_name": str(scope.get("atc_name") or "").strip() or None}
        if group == "RAG":
            email = str(scope.get("rag_email") or "").strip().casefold()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise ValueError("Un e-mail RAG valide est obligatoire.")
            return {"rag_email": email}
        if group == "DR":
            codes = scope.get("ugs", ())
            code = "-".join(codes) if isinstance(codes, (tuple, list)) else str(codes)
            code = re.sub(r"\s+", "", code)
            if not re.fullmatch(r"[0-9]{2}(?:-[0-9]{2})*", code):
                raise ValueError("Le périmètre DR doit contenir des UG comme 40-45-71-37.")
            return {"ugs": list(dict.fromkeys(code.split("-")))}
        if group == "ADMIN":
            return {}
        raise ValueError("Groupe invalide.")

    @staticmethod
    def _code(group, scope):
        return scope.get("atc_code", "") if group == "ATC" else scope.get("rag_email", "") if group == "RAG" else "-".join(scope.get("ugs", ())) if group == "DR" else ""

    def _duplicate(self, request, exclude=None):
        for other in self._pending():
            if other["id"] == exclude or other["statut"] not in OPEN:
                continue
            if request.get("objectId") and other.get("objectId"):
                match = request["objectId"] == other["objectId"]
            else:
                match = (_normalized(request["nom"]), _normalized(request["prenom"])) == (_normalized(other["nom"]), _normalized(other["prenom"]))
            if match:
                raise ValueError("Une demande est déjà en cours pour cette personne.")

    def create(self, actor, *, action, group, nom="", prenom="", principal_id=None, old_group=None, scope=None,
               catalog_id=None, rename_clients=False, vacant_city=None):
        self._actor(actor)
        if action not in {"AJOUTER", "MODIFIER", "SUPPRIMER"} or group not in GROUPS:
            raise ValueError("Action ou groupe invalide.")
        principal_id = object_id(principal_id) if principal_id else None
        if action != "AJOUTER" and not principal_id:
            raise ValueError("Un changement ou un retrait nécessite l'Object ID du compte.")
        if not principal_id and not (nom.strip() and prenom.strip()):
            raise ValueError("Le nom et le prénom sont obligatoires.")
        with self.documents.transaction():
            self._actor(actor)
            self.operations.recover(actor)
            self.pending()
            profile = self.profile(principal_id) if principal_id else None
            if profile:
                old_group = profile["role"].upper()
                if not nom.strip():
                    nom = profile.get("display_name") or principal_id
            if action == "SUPPRIMER" and old_group and old_group != group:
                raise ValueError("Le groupe à retirer doit correspondre au profil existant.")
            if action == "MODIFIER" and old_group not in GROUPS:
                raise ValueError("L'ancien groupe est obligatoire.")
            request_scope = {} if action == "SUPPRIMER" else self._scope(group, scope)
            if group == "ATC" and profile and request_scope.get("atc_code") != profile.get("atc_code") and not (action == "MODIFIER" and old_group == group):
                # Un ancien e-mail/nom prioritaire masquerait le changement de code.
                for field in ("atc_email", "atc_name"):
                    if request_scope.get(field) == profile.get(field):
                        request_scope[field] = None
            request = {
                "schema_version": 1, "id": uuid4().hex, "revision": 0, "action": action,
                "groupe": group, "ancienGroupe": old_group if action == "MODIFIER" else None,
                "nom": " ".join(nom.split()), "prenom": " ".join(prenom.split()),
                "objectId": principal_id, "utilisateur": {}, "scope": request_scope,
                "code": self._code(group, request_scope),
                "ancienCode": self._code(old_group, profile or {}),
                "statut": "EN_ATTENTE", "candidats": [], "dernierControle": None,
                "requested_by": actor.principal_id, "created_at": self._now(),
                "resolved_at": None, "resolved_by": None, "history": [],
            }
            if catalog_id:
                entry = self.catalog.get(catalog_id)
                if action == "SUPPRIMER" or not entry or entry["group"] != group or not entry.get("available") or entry.get("cancelled"):
                    raise ValueError("Ce prête-nom n'est pas disponible pour ce groupe.")
                if entry.get("assigned_to") not in {None, principal_id}:
                    raise ValueError("Ce périmètre a déjà été repris par un autre compte.")
                request["catalog_id"] = catalog_id
                request["catalog_name"] = entry["display_name"]
                if rename_clients:
                    name = f"{nom} {prenom}".strip() or (profile or {}).get("display_name", "")
                    if not name:
                        raise ValueError("Le nom du repreneur est obligatoire.")
                    if group == "ATC":
                        request["scope"]["atc_name"] = name
                    clients, _ = self.store.load_clients()
                    selected = catalog_clients(clients, entry)
                    request["portfolio_change"] = prepare_assignment(self.store, group, entry["scope"], name, request["scope"], selected_clients=selected)
                    if not request["portfolio_change"]:
                        raise ValueError("Aucun client retrouvé pour ce prête-nom. Vérifiez son périmètre avant la reprise.")
            if vacant_city is not None:
                if not vacant_city.strip():
                    raise ValueError("La ville du prête-nom est obligatoire.")
                if action != "SUPPRIMER" or not profile or group not in {"ATC", "RAG", "DR"}:
                    raise ValueError("Le passage à un prête-nom nécessite le retrait d'un profil métier existant.")
                name = f"{group} {' '.join(vacant_city.split())}"
                previous_scope = business_scope(group, profile)
                vacant_scope = {**previous_scope, **({"atc_name": name} if group == "ATC" else {})}
                request["vacancy"] = {"id": request["id"], "name": name, "scope": vacant_scope,
                                      "change": prepare_assignment(self.store, group, previous_scope, name)}
            if action == "MODIFIER" and group == old_group:
                if not profile or not profile["enabled"]:
                    raise ValueError("La modification directe nécessite un profil actif existant.")
                request["action"] = "MODIFIER_CODE"
                if group == "ATC" and not catalog_id:
                    request["scope"]["atc_name"] = request["scope"].get("atc_name") or profile.get("atc_name") or profile["display_name"]
                    request["scope"]["atc_email"] = request["scope"].get("atc_email") or profile.get("atc_email")
                    request["portfolio_change"] = prepare_assignment(self.store, group, business_scope(group, profile), request["scope"]["atc_name"], request["scope"])
            ids = assignment_ids(request.get("portfolio_change")) | assignment_ids(request.get("vacancy", {}).get("change"))
            for other in self._pending():
                if other["statut"] not in OPEN:
                    continue
                if catalog_id and other.get("catalog_id") == catalog_id:
                    raise ValueError("Une reprise est déjà en cours pour ce prête-nom.")
                other_ids = assignment_ids(other.get("portfolio_change")) | assignment_ids(other.get("vacancy", {}).get("change"))
                if ids & other_ids:
                    raise ValueError("Ces clients font déjà l'objet d'une réaffectation en cours.")
            self._duplicate(request)
            self._event(request, "CREEE")
            if principal_id:
                state = self.state(principal_id)
                request["previous_enabled"] = state.get("enabled", True)
                state["pending_request_id"] = request["id"]
                if action == "SUPPRIMER":
                    state.update(enabled=False, disabled_by_request_id=request["id"])
                    if group == "ADMIN" and not profile:
                        state.update(role="admin", admin_profile={"principal_id": principal_id, "display_name": request["nom"], "role": "admin"})
                    self._event(request, "PROFIL_DESACTIVE")
                state["_pending_request"] = copy.deepcopy(request)
                self._state(principal_id, state)
            self._save(request)
            if principal_id:
                state = self.state(principal_id)
                state.pop("_pending_request", None)
                self._state(principal_id, state)
            if request["action"] == "MODIFIER_CODE":
                self._finish(request, "ADMIN")
            elif request.get("vacancy"):
                self._maintain_vacancy(request)
            return request

    def _maintain_vacancy(self, request, *, available=False):
        vacancy = request["vacancy"]
        apply_assignment(self.store, vacancy["change"], request["id"], request["requested_by"])
        entry = self.catalog.get(vacancy["id"]) or {}
        self.catalog.put({**entry, "id": vacancy["id"], "group": request["groupe"],
                          "display_name": vacancy["name"], "scope": vacancy["scope"],
                          "origin": "Départ", "available": available, "cancelled": False,
                          "waiting_for": None if available else request["id"], "updated_at": self._now()})

    def _archive(self, request):
        month = request["resolved_at"][:7]
        key = f"access-requests/done/{month}/{request['id']}.json"
        previous, etag = self.documents.read(key)
        if previous is None:
            self.documents.write(key, request, None)
        elif previous != request:
            self.documents.write(key, request, etag)
        if request.get("objectId"):
            state = self.state(request["objectId"])
            if state.get("pending_request_id") == request["id"]:
                state.pop("pending_request_id", None)
                state.pop("_pending_request", None)
                self._state(request["objectId"], state)
        pending, etag = self.documents.read(PENDING + request["id"] + ".json")
        if pending:
            self.documents.delete(PENDING + request["id"] + ".json", etag)

    def _finish(self, request, proof):
        principal_id = request["objectId"]
        if not principal_id:
            raise ValueError("Une preuve doit identifier le compte.")
        state = self.state(principal_id)
        owner = state.get("pending_request_id")
        if owner and owner != request["id"]:
            raise StorageConflict("Une autre demande agit déjà sur ce compte.")
        profile = self.profile(principal_id)
        if request["action"] == "AJOUTER" and profile and profile["enabled"] and profile["role"].upper() != request["groupe"]:
            request.update(statut="A_PRECISER", motif=f"Profil déjà actif (groupe {profile['role'].upper()}) : utiliser Modifier")
            self._save(request)
            return
        if not request.get("application"):
            if request.get("portfolio_change"):
                name = f"{request['nom']} {request['prenom']}".strip()
                retarget_assignment(request["portfolio_change"], name)
                if request["groupe"] == "ATC":
                    request["scope"]["atc_name"] = name
            request["application"] = {"proof": proof, "date": self._now()}
            self._save(request)
        if request["action"] == "SUPPRIMER":
            state.update(enabled=False, disabled_by_request_id=request["id"])
            if request.get("vacancy"):
                self._maintain_vacancy(request, available=True)
            status = "CLOTUREE"
        else:
            apply_assignment(self.store, request.get("portfolio_change"), request["id"], request["requested_by"])
            if state.get("last_applied_request_id") != request["id"]:
                display_name = " ".join((request["nom"], request["prenom"])).strip() or principal_id
                if request["groupe"] == "ADMIN":
                    state["admin_profile"] = {"principal_id": principal_id, "display_name": display_name, "role": "admin"}
                else:
                    self.store.save_access_profile(
                        UserAccessProfile(principal_id=principal_id, display_name=display_name,
                                          role=request["groupe"].lower(), **request["scope"]),
                        updated_by=request["requested_by"], access_request_id=request["id"],
                    )
            state.update(enabled=True, role=request["groupe"].lower(), last_applied_request_id=request["id"])
            state.pop("disabled_by_request_id", None)
            status = "CONFIRMEE"
        self._state(principal_id, state)
        if request.get("catalog_id"):
            entry = self.catalog.get(request["catalog_id"])
            if entry is None:
                raise StorageConflict("Le prête-nom sélectionné n'est plus disponible.")
            self.catalog.put({**entry, "available": False, "assigned_to": principal_id, "resolved_by_request": request["id"], "updated_at": self._now()})
        request.update(statut=status, resolved_at=self._now(), resolved_by=proof)
        request.pop("application", None)
        self._event(request, status, proof=proof)
        self._save(request)
        self._archive(request)

    def check(self, actor, request_id):
        self._actor(actor)
        with self.documents.transaction():
            request = self.get(request_id)
            if not request or request["statut"] not in OPEN:
                return request
            if request.get("cancelling"):
                self._cancel(request)
                return request
            if request.get("application"):
                self._finish(request, request["application"]["proof"])
                return request
            if request["statut"] == "A_PRECISER":
                return request
            snapshot = copy.deepcopy(request)
        if self.flow is None:
            raise AdUnavailable("Le flux de vérification n'est pas configuré.")
        result = self.flow.verifier(snapshot)
        with self.documents.transaction():
            self._actor(actor)
            request = self.get(request_id)
            if not request or request["statut"] not in OPEN or request["revision"] != snapshot["revision"]:
                return request
            request["dernierControle"] = {"date": self._now(), "statutFlux": result.statut, "motif": result.motif, "execution": result.execution}
            if request["action"] == "AJOUTER" and result.object_id and not request.get("objectId"):
                resolved = {**request, "objectId": result.object_id}
                try:
                    self._duplicate(resolved, exclude=request_id)
                except ValueError as exc:
                    request.update(statut="A_PRECISER", motif=str(exc), candidats=list(result.candidats or ((result.utilisateur or {"objectId": result.object_id}),)))
                    self._save(request)
                    return request
                request.update(objectId=result.object_id, utilisateur=result.utilisateur)
                self._event(request, "OBJECTID_RESOLU")
                self._save(request)
                state = self.state(result.object_id)
                state["pending_request_id"] = request_id
                self._state(result.object_id, state)
            if result.statut in {"INTROUVABLE", "AMBIGU"} and request["action"] == "AJOUTER":
                request.update(statut="A_PRECISER", candidats=list(result.candidats), motif=result.motif)
            self._save(request)
            if result.statut == "APPLIQUEE":
                self._finish(request, "AD")
            return request

    def clarify(self, actor, request_id, *, candidate=None, nom=None, prenom=None):
        self._actor(actor)
        with self.documents.transaction():
            self._actor(actor)
            request = self.get(request_id)
            if not request or request["statut"] != "A_PRECISER" or request["action"] != "AJOUTER":
                raise ValueError("Cette demande ne peut pas être précisée.")
            if candidate is not None:
                if candidate not in request["candidats"]:
                    raise ValueError("Choisissez un candidat renvoyé par le flux.")
                proposal = {**request, "objectId": object_id(candidate["objectId"])}
                self._duplicate(proposal, exclude=request_id)
                state = self.state(proposal["objectId"])
                if state.get("pending_request_id") not in {None, request_id}:
                    raise ValueError("Une autre demande agit déjà sur ce compte.")
                if request.get("objectId") and request["objectId"] != proposal["objectId"]:
                    raise ValueError("Annulez la demande pour changer un compte déjà identifié.")
                request.update(objectId=proposal["objectId"], utilisateur=candidate)
                self._event(request, "CANDIDAT_CHOISI")
                self._save(request)
                state["pending_request_id"] = request_id
                self._state(request["objectId"], state)
            else:
                if request.get("objectId"):
                    raise ValueError("Annulez la demande pour changer une identité déjà résolue.")
                if not nom or not prenom:
                    raise ValueError("Nom et prénom obligatoires.")
                request.update(nom=" ".join(nom.split()), prenom=" ".join(prenom.split()))
                self._duplicate(request, exclude=request_id)
                self._event(request, "NOM_CORRIGE")
            request.update(statut="EN_ATTENTE", candidats=[], motif="")
            self._save(request)
        return self.check(actor, request_id)

    def cancel(self, actor, request_id):
        self._actor(actor)
        with self.documents.transaction():
            self._actor(actor)
            request = self.get(request_id)
            if not request or request["statut"] not in OPEN:
                return request
            if request.get("application"):
                self._finish(request, request["application"]["proof"])
                raise ValueError("La demande était déjà en cours d'application et a été finalisée.")
            request.update(cancelling=True, cancelled_by=actor.principal_id)
            self._save(request)
            self._cancel(request)
            return request

    def _cancel(self, request):
        if request.get("vacancy"):
            vacancy = request["vacancy"]
            apply_assignment(self.store, vacancy["change"], request["id"], request["cancelled_by"], reverse=True)
            entry = self.catalog.get(vacancy["id"])
            if entry:
                self.catalog.put({**entry, "available": False, "cancelled": True, "waiting_for": None})
        if request["action"] == "SUPPRIMER":
            state = self.state(request["objectId"])
            if state.get("disabled_by_request_id") == request["id"]:
                state["enabled"] = request.get("previous_enabled", True)
                state.pop("disabled_by_request_id", None)
                self._state(request["objectId"], state)
        request.update(statut="ANNULEE", resolved_at=self._now(), resolved_by="ADMIN")
        request.pop("cancelling", None)
        self._event(request, "ANNULEE", actor=request["cancelled_by"])
        self._save(request)
        self._archive(request)

    def on_login(self, user):
        if user.method != "entra" or user.issued_at is None or user.expires_at is None:
            return
        if not user.issued_at <= self.clock().timestamp() < user.expires_at:
            return
        confirmed = False
        with self.documents.transaction():
            for request in self._pending():
                if (request["statut"] == "EN_ATTENTE" and not request.get("cancelling") and request["action"] in {"AJOUTER", "MODIFIER"}
                    and request.get("objectId") == user.principal_id.casefold()
                    and request["groupe"] == user.role.upper()
                    and user.issued_at >= datetime.fromisoformat(request["created_at"]).timestamp()):
                    self._finish(request, "CONNEXION")
                    confirmed = True
        return confirmed

    def check_pending(self, actor, *, automatic=False):
        self._actor(actor)
        if self.flow is None:
            return []
        key = "access-requests/check-control.json"
        with self.documents.transaction():
            self._actor(actor)
            control, etag = self.documents.read(key)
            if automatic and control and self.clock() - datetime.fromisoformat(control["date"]) < timedelta(minutes=15):
                return []
            self.documents.write(key, {"date": self._now()}, etag)
            requests = [request for request in self.pending() if request["statut"] == "EN_ATTENTE"][:30]
        results, errors = [], 0
        started = self.timer()
        for request in requests:
            if self.timer() - started >= 120:
                break
            try:
                results.append((request["id"], self.check(actor, request["id"]), None))
                errors = 0
            except (AdUnavailable, RequeteFluxInvalide) as exc:
                errors += 1
                results.append((request["id"], None, str(exc)))
                if errors >= 3:
                    break
        return results
