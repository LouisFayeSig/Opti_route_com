from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from opti_route.storage import AppStore, RouteConfiguration, StorageError


def _clients(salesperson: str = "Morgan") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": ["A"],
            "client_name": ["Alpha"],
            "salesperson": [salesperson],
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
