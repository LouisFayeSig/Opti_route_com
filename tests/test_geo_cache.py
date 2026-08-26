from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from opti_route.cache import GeocodeCache
from opti_route.geo import clients_within_radius, estimated_road_matrix, haversine_km


def test_haversine_and_radius_selection() -> None:
    clients = pd.DataFrame(
        {
            "client_name": ["Proche", "Loin"],
            "latitude": [49.19, 48.8566],
            "longitude": [-0.37, 2.3522],
        }
    )
    selected = clients_within_radius(clients, 49.1829, -0.3707, radius_km=30)
    assert selected["client_name"].tolist() == ["Proche"]
    assert haversine_km(49.1829, -0.3707, 48.8566, 2.3522) == pytest.approx(200, rel=0.1)


def test_estimated_matrix_is_square_and_symmetric() -> None:
    durations, distances = estimated_road_matrix([(49.18, -0.37), (49.25, -0.20)])
    assert durations[0][0] == distances[0][0] == 0
    assert durations[0][1] == durations[1][0]
    assert distances[0][1] == distances[1][0]
    assert distances[0][1] > 0


def test_geocode_cache_round_trip() -> None:
    cache = GeocodeCache(Path(".cache/test-geocode-cache.sqlite3"))
    cache.set(" 1 Rue du Test, Caen ", 49.18, -0.37, "1 rue du Test, 14000 Caen")

    result = cache.get("1 rue du test, caen")

    assert result is not None
    assert result.latitude == pytest.approx(49.18)
    assert result.formatted_address == "1 rue du Test, 14000 Caen"
