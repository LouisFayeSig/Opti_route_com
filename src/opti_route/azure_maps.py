from __future__ import annotations

import io
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import requests
from PIL import Image as PillowImage
from PIL import ImageDraw
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .image_labels import draw_stop_labels


class AzureMapsError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str


@dataclass(frozen=True)
class StaticMapDiagnostic:
    """Résultat sûr à afficher d'un test de l'API de rendu statique."""

    available: bool
    status_code: int | None
    content_type: str | None
    request_id: str | None
    response_kind: str
    message: str


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
            raise AzureMapsError("Azure Maps est injoignable (erreur réseau ou proxy).") from exc

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

    def route_path(self, coordinates: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
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
        indexes = [
            round(position * (len(coordinates) - 1) / (limit - 1)) for position in range(limit)
        ]
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
        tile_size = 512
        zoom_longitude = math.log2(width * 0.75 * 360 / (tile_size * longitude_span))
        zoom_latitude = math.log2(
            height * 0.75 * 360 * latitude_factor / (tile_size * latitude_span)
        )
        # Une marge supplémentaire réserve de la place aux libellés ajoutés à l'image PDF.
        zoom = max(1, min(18, int(min(zoom_longitude, zoom_latitude))))
        return f"{center_longitude:.6f},{center_latitude:.6f}", zoom

    @staticmethod
    def _static_map_pixel(
        point: tuple[float, float],
        center: tuple[float, float],
        zoom: int,
        width: int,
        height: int,
    ) -> tuple[int, int]:
        """Projette une coordonnée GPS sur l'image Azure Maps (Web Mercator)."""

        def world_position(latitude: float, longitude: float) -> tuple[float, float]:
            latitude = max(-85.05112878, min(85.05112878, latitude))
            # Azure Maps road tiles use a 512 px tile grid at a given zoom level.
            world_size = 512 * (2**zoom)
            x = (longitude + 180) / 360 * world_size
            latitude_radians = math.radians(latitude)
            y = (
                (1 - math.asinh(math.tan(latitude_radians)) / math.pi)
                / 2
                * world_size
            )
            return x, y

        point_x, point_y = world_position(*point)
        center_x, center_y = world_position(*center)
        return round(width / 2 + point_x - center_x), round(height / 2 + point_y - center_y)

    def _static_base_map(self, center: str, zoom: int, width: int, height: int) -> bytes:
        """Récupère uniquement le fond Azure, sans données de tournée dans l'URL."""
        response = self._request(
            "GET",
            "/map/static",
            params={
                "api-version": "2024-04-01",
                "tilesetId": "microsoft.base.road",
                "center": center,
                "zoom": zoom,
                "width": width,
                "height": height,
                "language": "fr-FR",
            },
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

    def static_route_map(
        self,
        route_coordinates: Sequence[tuple[float, float]],
        geometry: Sequence[tuple[float, float]],
        return_to_start: bool,
        width: int = 1200,
        height: int = 650,
        stop_labels: Sequence[str] | None = None,
    ) -> bytes:
        if len(route_coordinates) < 2:
            raise AzureMapsError("La tournée ne contient pas assez de points pour créer une carte.")
        path_coordinates = self._downsample_path(geometry)
        view_coordinates = [*path_coordinates, *route_coordinates]
        center, zoom = self._static_map_view(view_coordinates, width, height)
        stops = list(route_coordinates)
        if return_to_start and stops[-1] == stops[0]:
            stops = stops[:-1]
        labels = list(stop_labels or ())
        labels.extend(str(index) for index in range(len(labels), len(stops)))

        # Évite les limites et filtrages potentiels de path/pins : le fond est demandé seul,
        # puis le tracé, les repères et les libellés sont superposés localement.
        image = PillowImage.open(io.BytesIO(self._static_base_map(center, zoom, width, height))).convert(
            "RGB"
        )
        center_longitude, center_latitude = (float(value) for value in center.split(","))
        map_center = (center_latitude, center_longitude)
        route_pixels = [
            self._static_map_pixel(point, map_center, zoom, width, height)
            for point in path_coordinates
        ]
        if len(route_pixels) > 1:
            draw = ImageDraw.Draw(image)
            draw.line(route_pixels, fill="white", width=11, joint="curve")
            draw.line(route_pixels, fill="#1565C0", width=7, joint="curve")

        stop_pixels = [
            self._static_map_pixel(point, map_center, zoom, width, height) for point in stops
        ]
        colors_by_stop = ["#1565C0", *["#D32F2F"] * (len(stops) - 1)]
        draw_stop_labels(image, stop_pixels, labels, colors_by_stop)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _diagnostic_header(response: requests.Response, name: str) -> str | None:
        value = response.headers.get(name)
        if not value:
            return None
        # Les identifiants Azure sont utiles au support, mais n'exposons jamais un en-tête libre.
        return re.sub(r"[^a-zA-Z0-9._:=/-]", "", str(value))[:128] or None

    def static_map_diagnostic(self) -> StaticMapDiagnostic:
        """Teste /map/static avec une zone neutre, sans divulguer la clé ni la réponse brute."""
        try:
            response = self._request(
                "GET",
                "/map/static",
                params={
                    "api-version": "2024-04-01",
                    "tilesetId": "microsoft.base.road",
                    "center": "2.3522,48.8566",
                    "zoom": 10,
                    "width": 320,
                    "height": 200,
                    "language": "fr-FR",
                },
                headers={**self._headers, "Accept": "image/png"},
            )
        except AzureMapsError as exc:
            return StaticMapDiagnostic(
                available=False,
                status_code=exc.status_code,
                content_type=None,
                request_id=None,
                response_kind="network_error",
                message=str(exc),
            )

        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        content_type = content_type or None
        request_id = self._diagnostic_header(response, "x-ms-request-id") or self._diagnostic_header(
            response, "x-ms-correlation-request-id"
        )
        is_png = response.content.startswith(b"\x89PNG")
        if response.status_code == 200 and is_png:
            return StaticMapDiagnostic(
                available=True,
                status_code=200,
                content_type=content_type,
                request_id=request_id,
                response_kind="png",
                message="La carte statique Azure Maps est accessible.",
            )

        response_kind = "html" if (
            content_type == "text/html" or response.content.lstrip().startswith(b"<")
        ) else "json" if content_type == "application/json" else "unexpected_response"
        message = (
            f"Azure Maps a répondu {response.status_code}."
            if response.status_code >= 400
            else "Azure Maps n'a pas renvoyé une image PNG valide."
        )
        return StaticMapDiagnostic(
            available=False,
            status_code=response.status_code,
            content_type=content_type,
            request_id=request_id,
            response_kind=response_kind,
            message=message,
        )
