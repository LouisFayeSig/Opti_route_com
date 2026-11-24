"""Client du contrat Power Automate v2, indépendant de Streamlit et des profils."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID

import requests
from azure.identity import DefaultAzureCredential

from .telemetry import emit

FLOW_SCOPE = "https://service.flow.microsoft.com//.default"
FLOW_TIMEOUT_SECONDS = 110
GROUPS = frozenset({"ATC", "RAG", "DR", "ADMIN"})
ACTIONS = frozenset({"AJOUTER", "MODIFIER", "SUPPRIMER"})
SUCCESS_STATUSES = frozenset({"APPLIQUEE", "EN_ATTENTE", "INTROUVABLE", "AMBIGU"})


class RequeteFluxInvalide(ValueError):
    """Le contrat d'appel est invalide ; aucune demande ne doit être modifiée."""


class AdUnavailable(RuntimeError):
    """Une vérification n'a pas fourni de preuve exploitable."""


def object_id(value: object) -> str:
    try:
        return str(UUID(str(value).strip()))
    except (ValueError, AttributeError) as exc:
        raise RequeteFluxInvalide("L'Object ID Entra est invalide.") from exc


def build_flow_payload(demande: Mapping) -> dict:
    request_id = str(demande.get("id") or demande.get("demandeId") or "")
    action = demande.get("action")
    group = demande.get("groupe")
    if not re.fullmatch(r"[0-9a-f]{32}", request_id) or action not in ACTIONS or group not in GROUPS:
        raise RequeteFluxInvalide("Identifiant, action ou groupe de demande invalide.")
    payload = {"version": 2, "demandeId": request_id, "action": action, "groupe": group}
    principal_id = demande.get("objectId")
    if principal_id:
        payload["objectId"] = object_id(principal_id)
    elif action == "AJOUTER":
        for field in ("nom", "prenom"):
            value = " ".join(str(demande.get(field) or "").split())
            if not value:
                raise RequeteFluxInvalide("Le nom et le prénom sont obligatoires pour un ajout.")
            payload[field] = value
    else:
        raise RequeteFluxInvalide("Un changement ou un retrait exige un Object ID Entra.")
    if action == "MODIFIER":
        previous = demande.get("ancienGroupe")
        if previous not in GROUPS or previous == group:
            raise RequeteFluxInvalide("Le changement doit viser un groupe différent.")
        payload["ancienGroupe"] = previous
    return payload


@dataclass(frozen=True)
class ResultatFlux:
    http_status: int
    statut: str
    motif: str
    object_id: str | None
    utilisateur: dict
    candidats: tuple[dict, ...]
    execution: str


def _json_field(value: object, expected: type):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, expected):
        raise TypeError("Format inattendu.")
    return value


def _person(value: object) -> dict:
    if not isinstance(value, dict):
        raise TypeError("Identité inattendue.")
    if not value:
        return {}
    result = {"objectId": object_id(value.get("objectId"))}
    for field in ("displayName", "upn", "mail"):
        raw = value.get(field)
        if raw is not None and not isinstance(raw, str):
            raise ValueError("Identité inattendue.")
        result[field] = (raw or "").strip()
    return result


def parse_flow_result(document: object, payload: Mapping) -> ResultatFlux:
    try:
        if not isinstance(document, dict) or type(document.get("version")) is not int:
            raise ValueError("Réponse inattendue.")
        if any(document.get(key) != payload[key] for key in ("version", "demandeId", "action", "groupe")):
            raise ValueError("Réponse d'une autre demande.")
        status = document.get("statut")
        if status not in SUCCESS_STATUSES:
            raise ValueError("Statut inconnu.")
        principal_id = object_id(document["objectId"]) if document.get("objectId") else None
        person = _person(_json_field(document.get("utilisateur", {}), dict))
        candidates = _json_field(document.get("candidats", []), list)
        if len(candidates) > 10:
            raise ValueError("Trop de candidats.")
        candidates = tuple(_person(candidate) for candidate in candidates)
        if any(not candidate for candidate in candidates):
            raise ValueError("Candidat sans identité.")
        if person and principal_id != person["objectId"]:
            raise ValueError("Identités incohérentes.")
        if payload.get("objectId") and principal_id and payload["objectId"] != principal_id:
            raise ValueError("Object ID différent de la demande.")
        if status == "APPLIQUEE" and principal_id is None:
            raise ValueError("Preuve sans Object ID.")
        motif = document.get("motif", "")
        execution = document.get("execution", "")
        if not isinstance(motif, str) or not isinstance(execution, str):
            raise TypeError("Texte inattendu.")
        if not re.fullmatch(r"[A-Za-z0-9._-]{0,200}", execution):
            raise ValueError("Identifiant d'exécution invalide.")
        return ResultatFlux(200, status, motif, principal_id, person, candidates, execution)
    except (ValueError, TypeError, KeyError) as exc:
        raise AdUnavailable("Réponse du flux invalide ou incohérente avec la demande.") from exc


class FlowClient:
    def __init__(
        self, url: str, *, credential=None, session=None,
        clock: Callable[[], float] = time.time,
        timer: Callable[[], float] = time.monotonic,
    ):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("FLOW_LECTURE_AD_URL doit être une URL HTTPS valide.")
        self._url = url
        self._credential = credential if credential is not None else DefaultAzureCredential()
        self._session = session if session is not None else requests.Session()
        self._clock = clock
        self._timer = timer
        self._token = None
        self._token_lock = Lock()

    def _bearer(self) -> str:
        with self._token_lock:
            if self._token is None or self._token.expires_on <= self._clock() + 300:
                self._token = self._credential.get_token(FLOW_SCOPE)
            return self._token.token

    def verifier(self, demande: Mapping) -> ResultatFlux:
        payload = build_flow_payload(demande)
        started = self._timer()
        http_status = None
        status = "INDISPONIBLE"
        execution = ""
        try:
            try:
                bearer = self._bearer()
            except Exception:
                raise AdUnavailable("L'identité de l'application ne peut pas appeler le flux.") from None
            try:
                response = self._session.post(
                    self._url, json=payload,
                    headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
                    timeout=FLOW_TIMEOUT_SECONDS, allow_redirects=False,
                )
            except requests.RequestException:
                raise AdUnavailable("Vérification indisponible, nouvel essai plus tard.") from None
            http_status = response.status_code
            if http_status == 400:
                status = "REQUETE_INVALIDE"
                raise RequeteFluxInvalide("Erreur technique, prévenir l'administrateur de l'application.")
            if http_status != 200:
                raise AdUnavailable("Vérification indisponible, nouvel essai plus tard.")
            try:
                document = response.json()
            except ValueError:
                raise AdUnavailable("Le flux a renvoyé une réponse illisible.") from None
            result = parse_flow_result(document, payload)
            status, execution = result.statut, result.execution
            return result
        finally:
            emit(
                "access_flow_check", request_id=payload["demandeId"], action=payload["action"],
                group=payload["groupe"], status=status, http_status=http_status,
                duration_ms=round((self._timer() - started) * 1000, 2), execution=execution,
            )
