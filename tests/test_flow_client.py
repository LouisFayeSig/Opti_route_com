import copy
from types import SimpleNamespace

import pytest
import requests

from opti_route.flow_client import (
    FLOW_SCOPE,
    AdUnavailable,
    FlowClient,
    RequeteFluxInvalide,
    build_flow_payload,
)

OID = "11111111-1111-4111-8111-111111111111"
REQUEST = {"id": "a" * 32, "action": "AJOUTER", "groupe": "ATC", "nom": "Dupont", "prenom": "Jean"}


class Session:
    def __init__(self, document=None, status=200, error=None):
        self.document = document
        self.status = status
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self

    @property
    def status_code(self):
        return self.status

    def json(self):
        if isinstance(self.document, Exception):
            raise self.document
        return self.document


class Credential:
    def __init__(self):
        self.calls = []

    def get_token(self, scope):
        self.calls.append(scope)
        return SimpleNamespace(token="SECRET_TOKEN", expires_on=1000 + len(self.calls) * 1000)


def response(status="APPLIQUEE", **changes):
    return {
        "version": 2, "demandeId": REQUEST["id"], "action": "AJOUTER", "groupe": "ATC",
        "statut": status, "motif": "État vérifié", "objectId": OID, "execution": "08584CU13",
        "utilisateur": {}, "candidats": [], **changes,
    }


def client(session, **kwargs):
    return FlowClient("https://flow.example.test/invoke?secret=URL_SECRET", session=session,
                      credential=Credential(), clock=lambda: 0, **kwargs)


@pytest.mark.parametrize("status", ["APPLIQUEE", "EN_ATTENTE", "INTROUVABLE", "AMBIGU"])
def test_known_statuses_are_parsed_and_only_contract_fields_are_sent(status):
    session = Session(response(status))
    request = {**REQUEST, "code": "31-02-004", "ancienCode": "53-01-03", "ignored": None}
    result = client(session).verifier(request)
    assert result.statut == status
    assert result.object_id == OID
    assert session.calls[0]["json"] == {
        "version": 2, "demandeId": REQUEST["id"], "action": "AJOUTER", "groupe": "ATC",
        "nom": "Dupont", "prenom": "Jean",
    }
    assert session.calls[0]["timeout"] == 110
    assert session.calls[0]["allow_redirects"] is False


def test_known_identity_omits_names_and_modification_includes_only_old_group():
    payload = build_flow_payload({**REQUEST, "objectId": OID})
    assert "nom" not in payload and "prenom" not in payload
    payload = build_flow_payload({**REQUEST, "objectId": OID, "action": "MODIFIER", "ancienGroupe": "RAG"})
    assert payload["ancienGroupe"] == "RAG"
    payload = build_flow_payload({**REQUEST, "objectId": OID, "action": "SUPPRIMER", "ancienGroupe": "RAG"})
    assert "ancienGroupe" not in payload


@pytest.mark.parametrize("changes", [
    {"id": "invalid"}, {"nom": ""}, {"prenom": ""}, {"groupe": "UNKNOWN"},
    {"action": "MODIFIER_CODE"}, {"action": "SUPPRIMER"},
    {"action": "MODIFIER", "objectId": OID, "ancienGroupe": "ATC"},
    {"action": "MODIFIER", "objectId": OID, "ancienGroupe": ""}, {"objectId": "invalid"},
])
def test_invalid_input_never_calls_the_flow(changes):
    session = Session()
    with pytest.raises(RequeteFluxInvalide):
        client(session).verifier({**REQUEST, **changes})
    assert session.calls == []


@pytest.mark.parametrize("status,exception", [(400, RequeteFluxInvalide), (500, AdUnavailable),
                                              (401, AdUnavailable), (302, AdUnavailable), (429, AdUnavailable)])
def test_http_errors_are_safe_and_do_not_mutate_the_request(status, exception):
    request = copy.deepcopy(REQUEST)
    with pytest.raises(exception):
        client(Session(status=status)).verifier(request)
    assert request == REQUEST


@pytest.mark.parametrize("error", [requests.Timeout("URL_SECRET"), requests.ConnectionError("SECRET_TOKEN")])
def test_transport_errors_are_safe(error):
    with pytest.raises(AdUnavailable) as caught:
        client(Session(error=error)).verifier(REQUEST)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("document", [
    ValueError("bad JSON"), [], {}, response("UNKNOWN"), response(version=1),
    response(demandeId="b" * 32), response(action="SUPPRIMER"), response(groupe="ADMIN"),
    response(objectId="invalid"), response(utilisateur="not JSON"), response(candidats={}),
    response(candidats=[{}]), response(execution="bad\nINJECTED"),
    response(utilisateur={"objectId": "22222222-2222-4222-8222-222222222222"}),
])
def test_malformed_or_uncorrelated_responses_are_rejected(document):
    with pytest.raises(AdUnavailable):
        client(Session(document)).verifier(REQUEST)


def test_json_encoded_people_are_decoded_and_resolved_oid_must_match():
    result = client(Session(response(
        utilisateur=f'{{"objectId":"{OID}","displayName":"PRIVATE_NAME"}}',
        candidats=f'[{{"objectId":"{OID}","mail":"private@example.test"}}]',
    ))).verifier(REQUEST)
    assert result.utilisateur["objectId"] == OID
    assert result.candidats[0]["mail"] == "private@example.test"
    with pytest.raises(AdUnavailable):
        client(Session(response())).verifier({**REQUEST, "objectId": "22222222-2222-4222-8222-222222222222"})


def test_token_scope_caching_and_early_renewal():
    session = Session(response())
    credential = Credential()
    now = [0]
    flow = FlowClient("https://flow.example.test", session=session, credential=credential, clock=lambda: now[0])
    flow.verifier(REQUEST)
    flow.verifier(REQUEST)
    assert credential.calls == [FLOW_SCOPE]
    now[0] = 1701  # Premier jeton valable jusqu'à 2000 : renouvellement cinq minutes avant.
    flow.verifier(REQUEST)
    assert credential.calls == [FLOW_SCOPE, FLOW_SCOPE]


def test_logs_contain_execution_and_no_identity_token_or_url(monkeypatch):
    events = []
    monkeypatch.setattr("opti_route.flow_client.emit", lambda event, **fields: events.append((event, fields)))
    client(Session(response(candidats=[{"objectId": OID, "displayName": "PRIVATE_NAME"}]))).verifier(REQUEST)
    event, fields = events[0]
    assert event == "access_flow_check"
    assert fields["execution"] == "08584CU13"
    assert fields["request_id"] == REQUEST["id"]
    assert all(value not in str(fields) for value in ("SECRET", "PRIVATE_NAME", "Dupont", OID, "https://"))
