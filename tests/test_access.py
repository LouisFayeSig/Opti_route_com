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
