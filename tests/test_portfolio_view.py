from __future__ import annotations

import pandas as pd
import pytest

from opti_route.access import authorize_portfolio
from opti_route.auth import AuthenticatedUser
from opti_route.portfolio_view import (
    SCOPE_AGENCY,
    SCOPE_ALL,
    SCOPE_COMMERCIAL,
    SCOPE_PORTFOLIOS,
    SCOPE_REGION,
    commercial_regions,
    default_map_scope,
    filter_map_clients,
    map_scope_options,
    salesperson_keys,
)
from opti_route.storage import UserAccessProfile


def _clients() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": ["A", "B", "C", "D"],
            "salesperson_code": ["01", "01", "02", "03"],
            "salesperson": ["Alice", "Bob", "Chloé", "David"],
            "salesperson_email": ["alice@example.test", "bob@example.test", "", ""],
            "agency": ["20-01", "24-01", "24-02", "40-01"],
            "ug": ["20", "24", "24", "40"],
        }
    )


def _profiles() -> list[UserAccessProfile]:
    return [
        UserAccessProfile("dr1", "DR Ouest", "dr", ugs=("20", "24")),
        UserAccessProfile("dr2", "DR IDF", "dr", ugs=("40",)),
    ]


def test_regions_group_the_ugs_of_dr_profiles_instead_of_treating_each_ug_as_a_region() -> None:
    regions = commercial_regions(_clients(), _profiles())

    assert set(regions) == {"20-24", "40"}
    assert regions["20-24"].ugs == ("20", "24")
    result = filter_map_clients(
        _clients(),
        role="admin",
        scope=SCOPE_REGION,
        active_salesperson="",
        regions=regions,
        region_keys=["20-24"],
    )
    assert result["client_id"].tolist() == ["A", "B", "C"]


def test_region_filters_cannot_expand_the_authorized_dr_perimeter() -> None:
    profile = _profiles()[0]
    allowed = authorize_portfolio(
        _clients(),
        AuthenticatedUser("DR", "entra", "dr", "dr1"),
        profile,
    ).clients
    regions = commercial_regions(allowed, _profiles())

    assert set(regions) == {"20-24"}
    result = filter_map_clients(
        allowed,
        role="dr",
        scope=SCOPE_REGION,
        active_salesperson="",
        regions=regions,
        region_keys=["20-24", "40"],
    )
    assert result["client_id"].tolist() == ["A", "B", "C"]


@pytest.mark.parametrize("role", ["atc", "rag"])
def test_region_scope_is_not_available_to_atc_or_rag(role: str) -> None:
    clients = _clients()
    regions = commercial_regions(clients, _profiles())
    assert SCOPE_REGION not in map_scope_options(role, clients, regions)
    assert filter_map_clients(
        clients,
        role=role,
        scope=SCOPE_REGION,
        active_salesperson="alice@example.test",
        regions=regions,
        region_keys=list(regions),
    ).empty


def test_atc_has_only_its_commercial_view() -> None:
    assert map_scope_options("atc", _clients(), {}) == (SCOPE_COMMERCIAL,)


def test_portfolio_filters_separate_people_with_the_same_atc_code() -> None:
    result = filter_map_clients(
        _clients(),
        role="rag",
        scope=SCOPE_PORTFOLIOS,
        active_salesperson="",
        portfolios=["bob@example.test"],
    )
    assert result["client_id"].tolist() == ["B"]
    keys = salesperson_keys(_clients())
    assert keys.iloc[2] == "code:02|name:chloé"


def test_the_commercial_scope_follows_the_selected_commercial() -> None:
    result = filter_map_clients(
        _clients(),
        role="admin",
        scope=SCOPE_COMMERCIAL,
        active_salesperson="bob@example.test",
    )
    assert result["client_id"].tolist() == ["B"]


def test_agency_filter_returns_its_commercial_portfolios() -> None:
    result = filter_map_clients(
        _clients(),
        role="rag",
        scope=SCOPE_AGENCY,
        active_salesperson="",
        agencies=["24-01", "24-02"],
    )
    assert result["client_id"].tolist() == ["B", "C"]


@pytest.mark.parametrize("scope", [SCOPE_PORTFOLIOS, SCOPE_AGENCY, SCOPE_REGION, "invalid"])
def test_empty_and_unknown_selections_never_fall_back_to_all_clients(scope: str) -> None:
    assert filter_map_clients(
        _clients(),
        role="admin",
        scope=scope,
        active_salesperson="",
        regions=commercial_regions(_clients(), _profiles()),
        region_keys=["outside"],
    ).empty


def test_missing_region_metadata_can_be_shown_explicitly() -> None:
    clients = _clients()
    clients.loc[3, "ug"] = pd.NA
    regions = commercial_regions(clients, _profiles())
    result = filter_map_clients(
        clients,
        role="admin",
        scope=SCOPE_REGION,
        active_salesperson="",
        regions=regions,
        region_keys=["unassigned"],
    )
    assert result["client_id"].tolist() == ["D"]


def test_default_scopes_follow_the_role_and_available_data() -> None:
    regions = commercial_regions(_clients(), _profiles())
    for role in ("dr", "admin"):
        assert default_map_scope(role, map_scope_options(role, _clients(), regions)) == SCOPE_REGION
    assert default_map_scope("rag", map_scope_options("rag", _clients(), {})) == SCOPE_PORTFOLIOS
    assert default_map_scope("admin", map_scope_options("admin", _clients(), {})) == SCOPE_ALL


def test_overlapping_regions_do_not_duplicate_clients() -> None:
    regions = commercial_regions(
        _clients(),
        [*_profiles(), UserAccessProfile("dr3", "DR Est", "dr", ugs=("24", "40"))],
    )
    result = filter_map_clients(
        _clients(),
        role="admin",
        scope=SCOPE_REGION,
        active_salesperson="",
        regions=regions,
        region_keys=list(regions),
    )
    assert result["client_id"].tolist() == ["A", "B", "C", "D"]
