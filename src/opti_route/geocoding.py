from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from .azure_maps import AzureMapsClient, AzureMapsError
from .cache import GeocodeCache

ProgressCallback = Callable[[int, int, str], None]


def geocode_missing_clients(
    clients: pd.DataFrame,
    azure_client: AzureMapsClient | None,
    cache: GeocodeCache,
    progress: ProgressCallback | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    enriched = clients.copy()
    missing_mask = enriched["latitude"].isna() | enriched["longitude"].isna()
    missing_indices = list(enriched.index[missing_mask])
    errors: list[str] = []
    total = len(missing_indices)

    for position, index in enumerate(missing_indices, start=1):
        raw_address = enriched.at[index, "full_address"]
        address = "" if pd.isna(raw_address) else str(raw_address).strip()
        client_name = str(enriched.at[index, "client_name"])
        if progress:
            progress(position, total, client_name)
        if not address:
            errors.append(f"{client_name} : adresse vide")
            continue

        cached = cache.get(address)
        if cached:
            enriched.at[index, "latitude"] = cached.latitude
            enriched.at[index, "longitude"] = cached.longitude
            continue
        if azure_client is None:
            errors.append(f"{client_name} : coordonnées absentes et Azure Maps non configuré")
            continue
        try:
            result = azure_client.geocode(address)
        except AzureMapsError as exc:
            errors.append(f"{client_name} : {exc}")
            continue
        enriched.at[index, "latitude"] = result.latitude
        enriched.at[index, "longitude"] = result.longitude
        cache.set(
            address,
            result.latitude,
            result.longitude,
            result.formatted_address,
        )
    return enriched, errors
