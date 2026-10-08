from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from opti_route.storage import (
    AppStore,
    RouteConfiguration,
    StorageError,
    UserAccessProfile,
)


def _clients(salesperson: str = "Morgan") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": ["A"],
            "client_name": ["Alpha"],
            "salesperson": [salesperson],
            "agency": ["Caen"],
            "agency_address": ["8 rue Ampère, 14120 Mondeville"],
            "address": ["1 rue du Test"],
            "address_2": [pd.NA],
            "address_3": [pd.NA],
            "postal_code": ["14000"],
            "city": ["Caen"],
            "country": ["France"],
            "latitude": [49.183],
            "longitude": [-0.370],
            "full_address": ["1 rue du Test, 14000, Caen, France"],
        }
    )


@pytest.fixture
def store_path() -> Path:
    path = Path(".cache") / f"storage-test-{uuid4().hex}.sqlite3"
    yield path
    for suffix in ("", "-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def test_store_round_trip_keeps_only_normalized_portfolio(store_path: Path) -> None:
    store = AppStore(store_path)

    metadata = store.save_clients(
        _clients(),
        source_name="../clients.xlsx",
        imported_by="Administrateur",
    )
    loaded, loaded_metadata = store.load_clients()

    assert loaded is not None
    assert loaded.loc[0, "client_name"] == "Alpha"
    assert loaded.loc[0, "salesperson"] == "Morgan"
    assert loaded.loc[0, "agency"] == "Caen"
    assert loaded.loc[0, "agency_address"] == "8 rue Ampère, 14120 Mondeville"
    assert metadata.source_name == "clients.xlsx"
    assert loaded_metadata == metadata


def test_store_rejects_a_portfolio_without_salesperson(store_path: Path) -> None:
    store = AppStore(store_path)

    with pytest.raises(StorageError, match="commercial"):
        store.save_clients(
            _clients("Tous"),
            source_name="clients.csv",
            imported_by="Administrateur",
        )


def test_admin_route_configuration_is_persistent_and_limited(store_path: Path) -> None:
    store = AppStore(store_path)
    configuration = RouteConfiguration(radius_km=50, max_visits=4, return_to_start=False)

    store.save_route_configuration(configuration)

    assert store.load_route_configuration() == configuration
    with pytest.raises(StorageError, match="compris entre 1 et 10"):
        store.save_route_configuration(RouteConfiguration(max_visits=11))


def test_access_profiles_are_persistent_and_revocable(store_path: Path) -> None:
    store = AppStore(store_path)
    profile = store.save_access_profile(
        UserAccessProfile(
            principal_id="OID-DIRECTION",
            display_name="Direction Caen",
            role="director",
            agencies=("Caen", "Bayeux"),
        ),
        updated_by="Admin",
    )

    loaded = store.load_access_profile("oid-direction")

    assert loaded == profile
    assert loaded is not None
    assert loaded.agencies == ("Caen", "Bayeux")
    assert store.list_access_profiles() == [profile]
    assert store.delete_access_profile("OID-DIRECTION")
    assert store.load_access_profile("oid-direction") is None


def test_access_profile_persists_imported_email_scope(store_path: Path) -> None:
    store = AppStore(store_path)
    profile = store.save_access_profile(
        UserAccessProfile(
            principal_id="OID-ATC",
            display_name="Alice",
            role="atc",
            atc_code="01",
            atc_email="Alice@Example.Test",
            atc_name="Alice",
        ),
        updated_by="Admin",
    )

    loaded = store.load_access_profile("oid-atc")

    assert loaded == profile
    assert loaded is not None
    assert loaded.atc_email == "alice@example.test"
    assert loaded.atc_name == "Alice"


def test_access_profile_requires_a_scope_matching_its_role(store_path: Path) -> None:
    store = AppStore(store_path)

    with pytest.raises(StorageError, match="code ATC"):
        store.save_access_profile(
            UserAccessProfile("oid-atc", "ATC", "atc"), updated_by="Admin"
        )
    with pytest.raises(StorageError, match="agence"):
        store.save_access_profile(
            UserAccessProfile("oid-director", "Direction", "director"),
            updated_by="Admin",
        )
