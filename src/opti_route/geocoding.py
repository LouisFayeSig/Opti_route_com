from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from .azure_maps import AzureMapsClient, AzureMapsError
from .cache import GeocodeCache
from .ign_geocoding import IgnGeocodingClient, IgnGeocodingError

ProgressCallback = Callable[[int, int, str], None]
PrimaryGeocodingClient = IgnGeocodingClient | AzureMapsClient


def geocode_missing_clients(
    clients: pd.DataFrame,
    geocoding_client: PrimaryGeocodingClient | None,
    cache: GeocodeCache,
    progress: ProgressCallback | None = None,
    fallback_client: AzureMapsClient | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Complete coordinates with the free IGN service and an optional Azure fallback."""

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
        if geocoding_client is None:
            errors.append(f"{client_name} : coordonnees absentes et geocodeur non configure")
            continue
        try:
            result = geocoding_client.geocode(address)
            provider = geocoding_client.provider
        except (AzureMapsError, IgnGeocodingError) as primary_error:
            if fallback_client is None:
                errors.append(f"{client_name} : {primary_error}")
                continue
            try:
                result = fallback_client.geocode(address)
                provider = fallback_client.provider
            except AzureMapsError as fallback_error:
                errors.append(f"{client_name} : {primary_error} Repli Azure Maps : {fallback_error}")
                continue
        enriched.at[index, "latitude"] = result.latitude
        enriched.at[index, "longitude"] = result.longitude
        cache.set(
            address,
            result.latitude,
            result.longitude,
            result.formatted_address,
            provider=provider,
        )
    return enriched, errors
