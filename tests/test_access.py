from __future__ import annotations

import pandas as pd
import pytest

from opti_route.access import AccessDeniedError, authorize_portfolio
from opti_route.auth import AuthenticatedUser
from opti_route.storage import UserAccessProfile


def _portfolio() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": ["1", "2", "3"],
            "client_name": ["Alpha", "Beta", "Gamma"],
            "salesperson_code": ["ATC-01", "ATC-02", "ATC-03"],
            "salesperson": ["Alice", "Bob", "Chloe"],
            "agency": ["Caen", "Caen", "Rouen"],
        }
    )


def test_atc_only_receives_clients_linked_to_its_code() -> None:
    user = AuthenticatedUser("Alice", "entra", "atc", "oid-alice")
    profile = UserAccessProfile("oid-alice", "Alice", "atc", atc_code="atc-01")

    authorized = authorize_portfolio(_portfolio(), user, profile)

    assert authorized.clients["client_name"].tolist() == ["Alpha"]


def test_director_receives_all_atcs_from_authorized_agencies() -> None:
    user = AuthenticatedUser("Direction", "entra", "director", "oid-direction")
    profile = UserAccessProfile(
        "oid-direction", "Direction", "director", agencies=("CAEN",)
    )

    authorized = authorize_portfolio(_portfolio(), user, profile)

    assert authorized.clients["client_name"].tolist() == ["Alpha", "Beta"]


def test_email_scopes_prevent_leaks_when_an_atc_code_is_reused() -> None:
    clients = pd.DataFrame(
        {
            "client_id": ["1", "2", "3", "4"],
            "client_name": ["Alice client", "Bob client", "Other client", "Director own client"],
            "salesperson_code": ["01", "01", "02", "03"],
            "salesperson_email": [
                "alice@example.test",
                "bob@example.test",
                "other@example.test",
                "director@example.test",
            ],
            "director_email": [
                "director@example.test",
                "director@example.test",
                "other-director@example.test",
                pd.NA,
            ],
            "agency": ["14", "76", "76", "14"],
        }
    )
    atc_user = AuthenticatedUser("Alice", "entra", "atc", "oid-alice")
    atc_profile = UserAccessProfile(
        "oid-alice",
        "Alice",
        "atc",
        atc_code="01",
        atc_email="alice@example.test",
    )
    director_user = AuthenticatedUser("Director", "entra", "director", "oid-director")
    director_profile = UserAccessProfile(
        "oid-director",
        "Director",
        "director",
        director_email="director@example.test",
    )

    atc_portfolio = authorize_portfolio(clients, atc_user, atc_profile)
    director_portfolio = authorize_portfolio(clients, director_user, director_profile)

    assert atc_portfolio.clients["client_name"].tolist() == ["Alice client"]
    assert director_portfolio.clients["client_name"].tolist() == [
        "Alice client",
        "Bob client",
        "Director own client",
    ]


def test_atc_name_keeps_legacy_code_scope_specific_when_email_is_missing() -> None:
    clients = pd.DataFrame(
        {
            "client_id": ["1", "2"],
            "client_name": ["Alice client", "Bob client"],
            "salesperson_code": ["01", "01"],
            "salesperson": ["Alice", "Bob"],
        }
    )
    user = AuthenticatedUser("Bob", "entra", "atc", "oid-bob")
    profile = UserAccessProfile(
        "oid-bob",
        "Bob",
        "atc",
        atc_code="01",
        atc_name="Bob",
    )

    authorized = authorize_portfolio(clients, user, profile)

    assert authorized.clients["client_name"].tolist() == ["Bob client"]


def test_ambiguous_legacy_atc_code_is_denied_without_a_name_or_email() -> None:
    clients = pd.DataFrame(
        {
            "client_id": ["1", "2"],
            "salesperson_code": ["01", "01"],
            "salesperson": ["Alice", "Bob"],
        }
    )
    user = AuthenticatedUser("Bob", "entra", "atc", "oid-bob")
    profile = UserAccessProfile("oid-bob", "Bob", "atc", atc_code="01")

    with pytest.raises(AccessDeniedError, match="ambigu"):
        authorize_portfolio(clients, user, profile)


def test_profile_cannot_elevate_or_change_the_entra_role() -> None:
    user = AuthenticatedUser("Alice", "entra", "atc", "oid-alice")
    profile = UserAccessProfile(
        "oid-alice", "Alice", "director", agencies=("Caen",)
    )

    with pytest.raises(AccessDeniedError, match="ne correspondent pas"):
        authorize_portfolio(_portfolio(), user, profile)


def test_non_admin_without_profile_is_denied() -> None:
    user = AuthenticatedUser("Alice", "entra", "atc", "oid-alice")

    with pytest.raises(AccessDeniedError, match="aucune habilitation"):
        authorize_portfolio(_portfolio(), user, None)


def test_admin_receives_the_complete_portfolio_without_profile() -> None:
    user = AuthenticatedUser("Admin", "entra", "admin", "oid-admin")

    authorized = authorize_portfolio(_portfolio(), user, None)

    assert authorized.clients["client_id"].tolist() == ["1", "2", "3"]
