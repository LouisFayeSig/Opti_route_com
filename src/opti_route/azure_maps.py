from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class AzureMapsError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str


class AzureMapsClient:
    API_VERSION = "2025-01-01"

    def __init__(self, endpoint: str, subscription_key: str, timeout_seconds: float = 30.0):
        self.endpoint = endpoint.rstrip("/")
        self.subscription_key = subscription_key
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    @property
    def _headers(self) -> dict[str, str]:
        return {"subscription-key": self.subscription_key, "Accept": "application/json"}

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.session.request(
                method,
                f"{self.endpoint}{path}",
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise AzureMapsError(f"Azure Maps est injoignable : {exc}") from exc
        return self._json_response(response)

    def _json_response(self, response: requests.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                payload = response.json()
                detail = payload.get("error", {}).get("message") or payload.get("detail", "")
            except ValueError:
                detail = response.text[:300]
            raise AzureMapsError(
                f"Azure Maps a répondu {response.status_code}. {detail}".strip()
            ) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise AzureMapsError("Azure Maps a renvoyé une réponse non JSON.") from exc

    def geocode(self, address: str) -> GeocodeResult:
        payload = self._request_json(
            "GET",
            "/geocode",
            params={"api-version": self.API_VERSION, "query": address, "top": 1},
            headers=self._headers,
        )
        features = payload.get("features", [])
        if features:
            feature = features[0]
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            if len(coordinates) >= 2:
                properties = feature.get("properties", {})
                address_data = properties.get("address", {})
                formatted = (
                    address_data.get("formattedAddress")
                    or properties.get("formattedAddress")
                    or address
                )
                return GeocodeResult(float(coordinates[1]), float(coordinates[0]), formatted)

        # Compatibilité avec le service Search v1 derrière un endpoint privé existant.
        results = payload.get("results", [])
        if results:
            position = results[0].get("position", {})
            if "lat" in position and "lon" in position:
                formatted = results[0].get("address", {}).get("freeformAddress", address)
                return GeocodeResult(float(position["lat"]), float(position["lon"]), formatted)
        raise AzureMapsError(f"Aucun résultat de géocodage pour « {address} ».")

    @staticmethod
    def _multipoint_feature(
        coordinates: Sequence[tuple[float, float]], point_type: str
    ) -> dict[str, Any]:
        return {
            "type": "Feature",
            "geometry": {
                "type": "MultiPoint",
                "coordinates": [[longitude, latitude] for latitude, longitude in coordinates],
            },
            "properties": {"pointType": point_type},
        }

    def route_matrix(
        self, coordinates: Sequence[tuple[float, float]]
    ) -> tuple[list[list[int]], list[list[int]]]:
        body = {
            "type": "FeatureCollection",
            "features": [
                self._multipoint_feature(coordinates, "origins"),
                self._multipoint_feature(coordinates, "destinations"),
            ],
            "optimizeRoute": "fastest",
            "traffic": "historical",
            "travelMode": "driving",
        }
        payload = self._request_json(
            "POST",
            "/route/matrix",
            params={"api-version": self.API_VERSION},
            json=body,
            headers={**self._headers, "Content-Type": "application/geo+json"},
        )
        size = len(coordinates)
        durations = [[0] * size for _ in range(size)]
        distances = [[0] * size for _ in range(size)]
        cells = payload.get("properties", {}).get("matrix", [])
        if not cells:
            raise AzureMapsError("La matrice Azure Maps est vide.")
        for cell in cells:
            origin = int(cell["originIndex"])
            destination = int(cell["destinationIndex"])
            if int(cell.get("statusCode", 200)) != 200:
                raise AzureMapsError(
                    f"Aucun itinéraire entre les points {origin + 1} et {destination + 1}."
                )
            durations[origin][destination] = round(
                float(cell.get("durationTrafficInSeconds") or cell["durationInSeconds"])
            )
            distances[origin][destination] = round(float(cell["distanceInMeters"]))
        return durations, distances

    @staticmethod
    def _line_coordinates(geometry: dict[str, Any] | None) -> list[tuple[float, float]]:
        if not geometry:
            return []
        coordinates = geometry.get("coordinates", [])
        geometry_type = geometry.get("type")
        if geometry_type == "LineString":
            return [(float(latitude), float(longitude)) for longitude, latitude, *_ in coordinates]
        if geometry_type == "MultiLineString":
            flattened: list[tuple[float, float]] = []
            for line in coordinates:
                part = [(float(latitude), float(longitude)) for longitude, latitude, *_ in line]
                if flattened and part and flattened[-1] == part[0]:
                    part = part[1:]
                flattened.extend(part)
            return flattened
        return []

    def route_path(
        self, coordinates: Sequence[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        features = []
        for index, (latitude, longitude) in enumerate(coordinates):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                    "properties": {"pointIndex": index, "pointType": "waypoint"},
                }
            )
        body = {
            "type": "FeatureCollection",
            "features": features,
            "optimizeRoute": "fastestWithTraffic",
            "routeOutputOptions": ["routePath"],
            "travelMode": "driving",
        }
        payload = self._request_json(
            "POST",
            "/route/directions",
            params={"api-version": self.API_VERSION},
            json=body,
            headers={**self._headers, "Content-Type": "application/geo+json"},
        )
        path = self._line_coordinates(payload.get("geometry"))
        route_features = payload.get("features", [])
        alternatives = payload.get("alternativeRoutes", [])
        if alternatives:
            # Azure Maps 2025 renvoie chaque proposition comme une FeatureCollection.
            route_features = alternatives[0].get("features", [])
        for feature in route_features:
            path.extend(self._line_coordinates(feature.get("geometry")))
        return path or list(coordinates)
