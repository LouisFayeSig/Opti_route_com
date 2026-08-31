from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    value = (
        math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(value))


def clients_within_radius(
    clients: pd.DataFrame, latitude: float, longitude: float, radius_km: float
) -> pd.DataFrame:
    valid = clients.dropna(subset=["latitude", "longitude"]).copy()
    if valid.empty:
        valid["straight_line_km"] = pd.Series(dtype=float)
        return valid

    latitudes = np.radians(valid["latitude"].astype(float).to_numpy())
    longitudes = np.radians(valid["longitude"].astype(float).to_numpy())
    start_latitude = math.radians(latitude)
    start_longitude = math.radians(longitude)
    dlat = latitudes - start_latitude
    dlon = longitudes - start_longitude
    haversine = (
        np.sin(dlat / 2) ** 2 + np.cos(start_latitude) * np.cos(latitudes) * np.sin(dlon / 2) ** 2
    )
    valid["straight_line_km"] = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(haversine))
    return valid[valid["straight_line_km"] <= radius_km].sort_values(
        "straight_line_km", kind="stable"
    )


def estimated_road_matrix(
    coordinates: Sequence[tuple[float, float]],
    road_factor: float = 1.25,
    average_speed_kmh: float = 45.0,
) -> tuple[list[list[int]], list[list[int]]]:
    """Retourne (durées en secondes, distances en mètres)."""
    size = len(coordinates)
    durations = [[0] * size for _ in range(size)]
    distances = [[0] * size for _ in range(size)]
    for origin in range(size):
        for destination in range(size):
            if origin == destination:
                continue
            distance_km = (
                haversine_km(*coordinates[origin], *coordinates[destination]) * road_factor
            )
            distances[origin][destination] = round(distance_km * 1000)
            durations[origin][destination] = round(distance_km / average_speed_kmh * 3600)
    return durations, distances
