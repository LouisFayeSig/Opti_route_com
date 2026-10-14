from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .azure_maps import GeocodeResult


class IgnGeocodingError(RuntimeError):
    """Erreur sure a afficher provenant du geocodeur public IGN/BAN."""


@dataclass
class IgnGeocodingClient:
    """Client du geocodeur IGN fonde sur la Base Adresse Nationale.

    Le service est gratuit pour les adresses francaises, mais sa limite publique est
    de 50 requetes par seconde et par IP. Le client applique donc un plafond plus
    prudent, configure a 40 requetes/s par defaut.
    """

    endpoint: str = "https://data.geopf.fr/geocodage/search"
    timeout_seconds: float = 30.0
    max_requests_per_second: float = 40.0

    provider = "IGN / Base Adresse Nationale"

    def __post_init__(self) -> None:
        self.endpoint = self.endpoint.rstrip("/")
        if self.max_requests_per_second <= 0 or self.max_requests_per_second > 50:
            raise ValueError("La cadence du geocodeur IGN doit etre comprise entre 0 et 50.")
        self._minimum_interval = 1 / self.max_requests_per_second
        self._last_request_at: float | None = None
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    @staticmethod
    def _safe_query(address: str) -> str:
        # Evite les caracteres susceptibles d'etre filtres par un proxy d'entreprise.
        cleaned = re.sub(r"['\"`<>;{}|\\\\]", " ", address)
        return " ".join(cleaned.split())

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self._minimum_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _request_json(self, address: str) -> dict[str, Any]:
        self._respect_rate_limit()
        try:
            response = self.session.get(
                self.endpoint,
                params={"q": self._safe_query(address), "limit": 1, "index": "address"},
                headers={
                    "Accept": "application/json",
                    "User-Agent": "OptiRoute/1.0 (geocodage import portefeuille)",
                },
                timeout=self.timeout_seconds,
            )
            self._last_request_at = time.monotonic()
        except requests.RequestException as exc:
            raise IgnGeocodingError(
                "Le service public IGN de geocodage est injoignable (erreur reseau ou proxy)."
            ) from exc
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            if response.status_code == 429:
                message = "Le service public IGN limite temporairement les requetes."
            else:
                message = f"Le service public IGN a repondu {response.status_code}."
            raise IgnGeocodingError(message) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise IgnGeocodingError("Le service public IGN a renvoye une reponse non JSON.") from exc
        if not isinstance(payload, dict):
            raise IgnGeocodingError("Le service public IGN a renvoye un format inattendu.")
        return payload

    @staticmethod
    def _parse(payload: dict[str, Any], fallback_address: str) -> GeocodeResult | None:
        features = payload.get("features")
        if not isinstance(features, list) or not features:
            return None
        first_feature = features[0]
        if not isinstance(first_feature, dict):
            return None
        geometry = first_feature.get("geometry", {})
        coordinates = geometry.get("coordinates", []) if isinstance(geometry, dict) else []
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            return None
        try:
            longitude, latitude = float(coordinates[0]), float(coordinates[1])
        except (TypeError, ValueError):
            return None
        properties = first_feature.get("properties", {})
        formatted_address = (
            properties.get("label", fallback_address)
            if isinstance(properties, dict)
            else fallback_address
        )
        return GeocodeResult(latitude, longitude, str(formatted_address))

    def geocode(self, address: str) -> GeocodeResult:
        payload = self._request_json(address)
        result = self._parse(payload, address)
        if result is None:
            raise IgnGeocodingError(f"Aucun resultat IGN/BAN pour \u00ab {address} \u00bb.")
        return result
