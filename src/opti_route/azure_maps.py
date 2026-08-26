from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class AzureMapsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        try:
            return self.session.request(
                method,
                f"{self.endpoint}{path}",
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise AzureMapsError(
                "Azure Maps est injoignable (erreur réseau ou proxy)."
            ) from exc

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._request(method, path, **kwargs)
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
                raw_text = response.text.lstrip()
                if raw_text.startswith("<"):
                    detail = "Réponse HTML reçue d'un proxy ou d'une règle réseau."
                else:
                    detail = response.text[:200]
            raise AzureMapsError(
                f"Azure Maps a répondu {response.status_code}. {detail}".strip(),
                status_code=response.status_code,
            ) from exc
        try:
            return response.json()
        except ValueError as exc:
            raise AzureMapsError("Azure Maps a renvoyé une réponse non JSON.") from exc

    @staticmethod
    def _safe_geocode_query(address: str) -> str:
        # Certains proxies d'entreprise bloquent les apostrophes et chevrons dans la query string.
        cleaned = re.sub(r"['\"`<>;{}|\\]", " ", address)
        return " ".join(cleaned.split())

    @staticmethod
    def _parse_geocode(payload: dict[str, Any], fallback_address: str) -> GeocodeResult | None:
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
                    or fallback_address
                )
                return GeocodeResult(float(coordinates[1]), float(coordinates[0]), formatted)

        # Compatibilité avec le service Search v1 derrière un endpoint privé existant.
        results = payload.get("results", [])
        if results:
            position = results[0].get("position", {})
            if "lat" in position and "lon" in position:
                formatted = results[0].get("address", {}).get("freeformAddress", fallback_address)
                return GeocodeResult(float(position["lat"]), float(position["lon"]), formatted)
        return None

    def geocode(self, address: str) -> GeocodeResult:
        safe_query = self._safe_geocode_query(address)
        payload = self._request_json(
            "GET",
            "/geocode",
            params={"api-version": self.API_VERSION, "query": safe_query, "top": 1},
            headers=self._headers,
        )
        result = self._parse_geocode(payload, address)
        if result is not None:
            return result
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

    @staticmethod
    def _downsample_path(
        coordinates: Sequence[tuple[float, float]], limit: int = 100
    ) -> list[tuple[float, float]]:
        if len(coordinates) <= limit:
            return list(coordinates)
        indexes = [round(position * (len(coordinates) - 1) / (limit - 1)) for position in range(limit)]
        return [coordinates[index] for index in indexes]

    @staticmethod
    def _static_map_view(
        coordinates: Sequence[tuple[float, float]], width: int, height: int
    ) -> tuple[str, int]:
        latitudes = [latitude for latitude, _ in coordinates]
        longitudes = [longitude for _, longitude in coordinates]
        center_latitude = (min(latitudes) + max(latitudes)) / 2
        center_longitude = (min(longitudes) + max(longitudes)) / 2
        latitude_span = max(max(latitudes) - min(latitudes), 0.005)
        longitude_span = max(max(longitudes) - min(longitudes), 0.005)
        latitude_factor = max(0.2, abs(math.cos(math.radians(center_latitude))))
        zoom_longitude = math.log2(width * 0.75 * 360 / (256 * longitude_span))
        zoom_latitude = math.log2(
            height * 0.75 * 360 * latitude_factor / (256 * latitude_span)
        )
        zoom = max(1, min(18, int(min(zoom_longitude, zoom_latitude))))
        return f"{center_longitude:.6f},{center_latitude:.6f}", zoom

    def static_route_map(
        self,
        route_coordinates: Sequence[tuple[float, float]],
        geometry: Sequence[tuple[float, float]],
        return_to_start: bool,
        width: int = 1200,
        height: int = 650,
    ) -> bytes:
        if len(route_coordinates) < 2:
            raise AzureMapsError("La tournée ne contient pas assez de points pour créer une carte.")
        path_coordinates = self._downsample_path(geometry)
        center, zoom = self._static_map_view(path_coordinates, width, height)
        path_value = "lc1565C0|lw5|la0.85||" + "|".join(
            f"{longitude:.6f} {latitude:.6f}" for latitude, longitude in path_coordinates
        )
        stops = list(route_coordinates)
        if return_to_start and stops[-1] == stops[0]:
            stops = stops[:-1]
        start_latitude, start_longitude = stops[0]
        params: list[tuple[str, str | int]] = [
            ("api-version", "2024-04-01"),
            ("tilesetId", "microsoft.base.road"),
            ("center", center),
            ("zoom", zoom),
            ("width", width),
            ("height", height),
            ("language", "fr-FR"),
            ("path", path_value),
            ("pins", f"default|co1565C0||{start_longitude:.6f} {start_latitude:.6f}"),
        ]
        visit_stops = stops[1:]
        if visit_stops:
            red_stops = visit_stops if return_to_start else visit_stops[:-1]
            if red_stops:
                params.append(
                    (
                        "pins",
                        "default|coD32F2F||"
                        + "|".join(
                            f"{longitude:.6f} {latitude:.6f}"
                            for latitude, longitude in red_stops
                        ),
                    )
                )
            if not return_to_start:
                latitude, longitude = visit_stops[-1]
                params.append(
                    ("pins", f"default|co2E7D32||{longitude:.6f} {latitude:.6f}")
                )
        response = self._request(
            "GET",
            "/map/static",
            params=params,
            headers={**self._headers, "Accept": "image/png"},
        )
        try:
            response.raise_for_status()
        except requests.HTTPError:
            self._json_response(response)
            raise AssertionError("unreachable")
        if not response.content.startswith(b"\x89PNG"):
            raise AzureMapsError("Azure Maps n'a pas renvoyé une image PNG valide.")
        return response.content
