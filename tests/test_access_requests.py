from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from opti_route.access_documents import StorageConflict
from opti_route.access_requests import AccessRequests
from opti_route.auth import AuthenticatedUser
from opti_route.flow_client import AdUnavailable, FlowClient, ResultatFlux
from opti_route.storage import AppStore, UserAccessProfile

OID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ADMIN = AuthenticatedUser("Admin", "entra", "admin", "33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)


class Flow:
    def __init__(self, status="APPLIQUEE", oid=OID, people=()):
        self.status, self.oid, self.people = status, oid, people
        self.calls = []
        self.effect = None

    def verifier(self, request):
        self.calls.append(request)
        if self.effect:
            self.effect()
        if isinstance(self.status, Exception):
            raise self.status
        return ResultatFlux(200, self.status, "Contrôle", self.oid, {}, self.people, "run-1")


@pytest.fixture
def service():
    path = Path(".cache") / f"access-requests-test-{uuid4().hex}.sqlite3"
    store = AppStore(path)
    value = AccessRequests(store, Flow(), clock=lambda: NOW)
    yield value
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def create(service, **changes):
    return service.create(ADMIN, **{
        "action": "AJOUTER", "group": "ATC", "nom": "Dupont", "prenom": "Jean",
        "scope": {"atc_code": "31-02-004"}, **changes,
    })


def seed(service, oid=OID, **fields):
    service.store.save_access_profile(UserAccessProfile(
        principal_id=oid, display_name="Jean Dupont", role="atc", atc_code="53-01-03", **fields,
    ), updated_by="test")


def test_add_is_pending_until_proof_then_persisted_and_archived(service):
    request = create(service)
    assert service.store.load_access_profile(OID) is None
    checked = service.check(ADMIN, request["id"])
    assert checked["statut"] == "CONFIRMEE"
    assert checked["resolved_by"] == "AD"
    assert service.store.load_access_profile(OID).atc_code == "31-02-004"
    assert service.get(request["id"]) is None
    assert service.recent()[0]["id"] == request["id"]
    service.check(ADMIN, request["id"])
    assert len(service.flow.calls) == 1


def test_flow_resolves_name_then_confirms_known_oid_with_null_user(service):
    calls = []

    def post(url, **kwargs):
        payload = kwargs["json"]
        calls.append(payload)
        body = {**{key: payload[key] for key in ("version", "demandeId", "action", "groupe")},
                "objectId": OID, "statut": "EN_ATTENTE" if len(calls) == 1 else "APPLIQUEE",
                "motif": "Pas encore membre du groupe" if len(calls) == 1 else "Membre du groupe",
                "utilisateur": {"objectId": OID, "displayName": "Jean Dupont"} if len(calls) == 1 else None,
                "candidats": [], "execution": f"run-{len(calls)}"}
        return SimpleNamespace(status_code=200, json=lambda: body)

    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test-token", expires_on=4102444800))
    service.flow = FlowClient("https://flow.example.test", credential=credential, session=SimpleNamespace(post=post))
    request = create(service)
    first = service.check(ADMIN, request["id"])
    assert first["statut"] == "EN_ATTENTE"
    assert first["objectId"] == OID
    assert service.store.load_access_profile(OID) is None
    second = service.check(ADMIN, request["id"])
    assert calls[1]["objectId"] == OID
    assert "nom" not in calls[1] and "prenom" not in calls[1]
    assert second["statut"] == "CONFIRMEE"
    assert second["resolved_by"] == "AD"
    assert second["dernierControle"]["statutFlux"] == "APPLIQUEE"
    assert service.store.load_access_profile(OID).atc_code == "31-02-004"
    assert service.get(request["id"]) is None
    assert service.recent()[0]["statut"] == "CONFIRMEE"


@pytest.mark.parametrize("code", ["53-01-03", "31-02-004", "legacy-code"])
def test_existing_atc_formats_are_preserved(service, code):
    request = create(service, scope={"atc_code": code})
    assert request["code"] == code


def test_rag_uses_email_and_dr_uses_ugs(service):
    request = create(service, group="RAG", scope={"rag_email": " Chef@Example.fr "})
    service.check(ADMIN, request["id"])
    assert service.store.load_access_profile(OID).rag_email == "chef@example.fr"
    request = create(service, action="MODIFIER", group="DR", principal_id=OID, scope={"ugs": "40-45-71"})
    service.check(ADMIN, request["id"])
    assert service.store.load_access_profile(OID).ugs == ("40", "45", "71")


@pytest.mark.parametrize("scope", [{"ugs": "40-"}, {"ugs": "4-45"}, {"ugs": "40_45"}])
def test_invalid_dr_scope_is_rejected(service, scope):
    with pytest.raises(ValueError):
        create(service, group="DR", scope=scope)


def test_remove_disables_immediately_and_cancel_restores_only_its_own_change(service):
    seed(service)
    request = create(service, action="SUPPRIMER", principal_id=OID)
    assert service.blocked(OID)
    assert service.store.load_access_profile(OID) is not None
    service.cancel(ADMIN, request["id"])
    assert not service.blocked(OID)
    assert service.recent()[0]["statut"] == "ANNULEE"


def test_cancel_never_reactivates_a_previously_disabled_profile(service):
    seed(service)
    service._state(OID, {"enabled": False})
    request = create(service, action="SUPPRIMER", principal_id=OID)
    service.cancel(ADMIN, request["id"])
    assert service.blocked(OID)


def test_remove_admin_without_a_business_profile_creates_a_deny_record(service):
    request = create(service, action="SUPPRIMER", group="ADMIN", principal_id=OID)
    assert service.blocked(OID)
    service.check(ADMIN, request["id"])
    assert service.blocked(OID)
    assert service.recent()[0]["statut"] == "CLOTUREE"


def test_admin_add_does_not_need_a_business_scope(service):
    request = create(service, group="ADMIN", scope={})
    service.check(ADMIN, request["id"])
    assert service.profile(OID)["role"] == "admin"
    assert service.store.load_access_profile(OID) is None


def test_scope_only_is_applied_directly_and_traced_without_flow(service):
    seed(service)
    request = create(service, action="MODIFIER", principal_id=OID)
    assert request["action"] == "MODIFIER_CODE"
    assert request["resolved_by"] == "ADMIN"
    assert service.store.load_access_profile(OID).atc_code == "31-02-004"
    assert service.flow.calls == []


@pytest.mark.parametrize("action", ["MODIFIER", "SUPPRIMER"])
def test_changes_and_removal_require_oid_not_names(service, action):
    with pytest.raises(ValueError):
        create(service, action=action)


def test_duplicate_unknown_names_and_known_oid_are_blocked(service):
    request = create(service)
    with pytest.raises(ValueError, match="déjà"):
        create(service, nom=" DUPONT ", prenom="jéan")
    service.flow.status = "EN_ATTENTE"
    service.check(ADMIN, request["id"])
    with pytest.raises(ValueError, match="déjà"):
        create(service, principal_id=OID, nom="Autre", prenom="Nom")


def test_resolved_identity_is_saved_even_while_waiting(service):
    service.flow.status = "EN_ATTENTE"
    request = create(service)
    service.check(ADMIN, request["id"])
    assert service.get(request["id"])["objectId"] == OID
    service.check(ADMIN, request["id"])
    assert service.flow.calls[-1]["objectId"] == OID


@pytest.mark.parametrize("status", ["INTROUVABLE", "AMBIGU"])
def test_candidates_can_be_selected_then_verified_by_oid(service, status):
    person = {"objectId": OID, "displayName": "Jean Dupont", "mail": "jean@example.fr"}
    service.flow = Flow(status, None, (person,))
    request = create(service)
    service.check(ADMIN, request["id"])
    assert service.get(request["id"])["statut"] == "A_PRECISER"
    service.flow.status, service.flow.oid = "APPLIQUEE", OID
    service.clarify(ADMIN, request["id"], candidate=person)
    assert service.flow.calls[-1]["objectId"] == OID
    assert service.recent()[0]["statut"] == "CONFIRMEE"


def test_add_in_another_existing_active_group_requires_modify(service):
    seed(service)
    request = create(service, group="RAG", scope={"rag_email": "chef@example.fr"})
    service.check(ADMIN, request["id"])
    assert service.get(request["id"])["statut"] == "A_PRECISER"
    assert service.store.load_access_profile(OID).role == "atc"


def test_a_flow_result_arriving_after_cancellation_cannot_create_a_profile(service):
    request = create(service)
    service.flow.effect = lambda: service.cancel(ADMIN, request["id"])
    service.check(ADMIN, request["id"])
    assert service.store.load_access_profile(OID) is None
    assert service.recent()[0]["statut"] == "ANNULEE"


def test_flow_errors_leave_the_request_unchanged(service):
    request = create(service)
    before = service.get(request["id"])
    service.flow.status = AdUnavailable("indisponible")
    with pytest.raises(AdUnavailable):
        service.check(ADMIN, request["id"])
    assert service.get(request["id"]) == before


def test_import_cannot_overwrite_a_profile_while_request_is_pending(service):
    seed(service)
    create(service, action="SUPPRIMER", principal_id=OID)
    with pytest.raises(StorageConflict):
        seed(service)
    assert service.blocked(OID)


def test_non_admin_cannot_mutate_a_request(service):
    with pytest.raises(PermissionError):
        service.create(AuthenticatedUser("ATC", "entra", "atc"), action="AJOUTER", group="ADMIN")


def test_etag_cas_rejects_stale_updates_and_sqlite_rolls_back_all_documents(service):
    documents = service.documents
    etag = documents.write("access-requests/test.json", {"value": 1}, None)
    documents.write("access-requests/test.json", {"value": 2}, etag)
    with pytest.raises(StorageConflict):
        documents.write("access-requests/test.json", {"value": 3}, etag)
    with pytest.raises(RuntimeError), documents.transaction():
        documents.write("access-state/rollback.json", {}, None)
        raise RuntimeError
    assert documents.read("access-state/rollback.json") == (None, None)


def test_batch_stops_after_three_errors_and_automatic_check_has_shared_cooldown(service):
    for index in range(4):
        create(service, nom=f"Personne{index}")
    service.flow.status = AdUnavailable("indisponible")
    assert len(service.check_pending(ADMIN, automatic=True)) == 3
    other_session = AccessRequests(service.store, service.flow, clock=lambda: NOW)
    assert other_session.check_pending(ADMIN, automatic=True) == []


@pytest.mark.parametrize("action", ["AJOUTER", "MODIFIER"])
def test_fresh_entra_login_is_a_proof_for_exact_identity_and_target_role(service, action):
    if action == "MODIFIER":
        seed(service)
    request = create(service, action=action, principal_id=OID, group="RAG", scope={"rag_email": "chef@example.fr"})
    user = AuthenticatedUser("Jean", "entra", "rag", OID, issued_at=NOW.timestamp(), expires_at=NOW.timestamp() + 3600)
    service.on_login(user)
    assert service.get(request["id"]) is None
    assert service.recent()[0]["resolved_by"] == "CONNEXION"
    assert service.store.load_access_profile(OID).rag_email == "chef@example.fr"
    service.on_login(user)
    assert len(service.recent()) == 1
    assert service.flow.calls == []


@pytest.mark.parametrize("changes", [
    {"method": "password"}, {"principal_id": OTHER}, {"role": "admin"},
    {"issued_at": None}, {"expires_at": None}, {"issued_at": NOW.timestamp() - 1},
    {"issued_at": NOW.timestamp() + 1}, {"expires_at": NOW.timestamp()},
])
def test_old_invalid_or_unrelated_login_is_not_a_proof(service, changes):
    request = create(service, principal_id=OID)
    user = AuthenticatedUser(**{"display_name": "Jean", "method": "entra", "role": "atc",
                                "principal_id": OID, "issued_at": NOW.timestamp(),
                                "expires_at": NOW.timestamp() + 3600, **changes})
    service.on_login(user)
    assert service.get(request["id"])["statut"] == "EN_ATTENTE"
    assert service.store.load_access_profile(OID) is None


def test_login_never_confirms_removal_or_resolves_unknown_names(service):
    request = create(service)
    user = AuthenticatedUser("Jean", "entra", "atc", OID, issued_at=NOW.timestamp(), expires_at=NOW.timestamp() + 3600)
    service.on_login(user)
    assert service.get(request["id"])["objectId"] is None
    service.cancel(ADMIN, request["id"])
    seed(service)
    request = create(service, action="SUPPRIMER", principal_id=OID)
    service.on_login(user)
    assert service.get(request["id"])["statut"] == "EN_ATTENTE"
    assert service.blocked(OID)


def test_changing_only_atc_code_preserves_commercial_identity(service):
    seed(service, atc_email="old@example.fr", atc_name="Ancien commercial")
    create(service, action="MODIFIER", principal_id=OID,
           scope={"atc_code": "31-02-004", "atc_email": "old@example.fr", "atc_name": "Ancien commercial"})
    profile = service.store.load_access_profile(OID)
    assert profile.atc_code == "31-02-004"
    assert profile.atc_email == "old@example.fr"
    assert profile.atc_name == "Ancien commercial"


def test_stale_admin_token_cannot_bypass_a_confirmed_demotion(service):
    create(service, group="ADMIN", principal_id=OID)
    request = service.pending()[0]
    service.check(ADMIN, request["id"])
    request = create(service, action="MODIFIER", principal_id=OID, old_group="ADMIN")
    service.check(ADMIN, request["id"])
    stale = AuthenticatedUser("Ancien admin", "entra", "admin", OID)
    with pytest.raises(PermissionError, match="groupe a changé"):
        service.ensure_session(stale)
    with pytest.raises(PermissionError):
        service.create(stale, action="AJOUTER", group="ADMIN", nom="Autre", prenom="Compte")


def test_prepared_application_can_be_retried_after_profile_storage_error(service, monkeypatch):
    request = create(service)
    save = service.store.save_access_profile
    monkeypatch.setattr(service.store, "save_access_profile", lambda *args, **kwargs: (_ for _ in ()).throw(StorageConflict("indisponible")))
    with pytest.raises(StorageConflict):
        service.check(ADMIN, request["id"])
    assert service.store.load_access_profile(OID) is None
    monkeypatch.setattr(service.store, "save_access_profile", save)
    service.check(ADMIN, request["id"])
    assert service.recent()[0]["statut"] == "CONFIRMEE"
    assert service.store.load_access_profile(OID) is not None


def test_parallel_creation_only_accepts_one_open_request_for_a_person(service):
    from concurrent.futures import ThreadPoolExecutor

    def attempt(_):
        try:
            return create(service, principal_id=OID)["id"]
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = list(executor.map(attempt, range(2)))
    assert len([value for value in accepted if value]) == 1
    assert len(service.pending()) == 1
