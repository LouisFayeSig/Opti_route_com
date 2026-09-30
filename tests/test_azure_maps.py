from __future__ import annotations

import io
from typing import Any

import pytest
import requests
from PIL import Image as PillowImage

from opti_route.azure_maps import AzureMapsClient, AzureMapsError


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ):
        self.payload = payload
        self.status_code = status_code
        self.text = ""
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP error", response=self)

    def json(self) -> dict[str, Any]:
        if self.payload is None:
            raise ValueError("not JSON")
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]):
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url: str, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _solid_png(width: int = 1200, height: int = 650, color: str = "#31536D") -> bytes:
    """Return a real PNG so local map compositing is exercised, not just the PNG signature."""
    output = io.BytesIO()
    PillowImage.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def _params_as_dict(params: Any) -> dict[str, str | int]:
    return dict(params.items()) if isinstance(params, dict) else dict(params)


def test_geocoding_uses_2025_api_and_lon_lat_order() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "features": [
                        {
                            "geometry": {"type": "Point", "coordinates": [-0.3707, 49.1829]},
                            "properties": {"address": {"formattedAddress": "Caen, France"}},
                        }
                    ]
                }
            )
        ]
    )
    client = AzureMapsClient("https://example.test", "secret")
    client.session = session

    result = client.geocode("Caen")

    assert result.latitude == 49.1829
    assert result.longitude == -0.3707
    assert session.calls[0][1] == "https://example.test/geocode"
    assert session.calls[0][2]["params"]["api-version"] == "2025-01-01"


def test_geocoding_sanitizes_apostrophes_blocked_by_proxies() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "features": [
                        {
                            "geometry": {"type": "Point", "coordinates": [-0.33, 49.20]},
                            "properties": {},
                        }
                    ]
                }
            )
        ]
    )
    client = AzureMapsClient("https://example.test", "secret")
    client.session = session

    client.geocode("10 RUE D'ATALENTE, 'LE CLOS', France")

    assert session.calls[0][2]["params"]["query"] == "10 RUE D ATALENTE, LE CLOS , France"


def test_html_error_is_replaced_by_safe_message() -> None:
    session = FakeSession(
        [
            FakeResponse(
                None,
                status_code=403,
            )
        ]
    )
    session.responses[0].text = "<!DOCTYPE html><html>secret proxy details</html>"
    client = AzureMapsClient("https://example.test", "secret")
    client.session = session

    with pytest.raises(AzureMapsError) as caught:
        client.geocode("Adresse")

    assert "Réponse HTML reçue" in str(caught.value)
    assert "DOCTYPE" not in str(caught.value)


def test_proxy_error_is_replaced_by_short_message() -> None:
    client = AzureMapsClient("https://example.test", "secret")
    client.session = FakeSession([requests.exceptions.ProxyError("internal proxy details")])

    with pytest.raises(AzureMapsError) as caught:
        client.geocode("Adresse")

    assert str(caught.value) == "Azure Maps est injoignable (erreur réseau ou proxy)."
    assert "internal proxy details" not in str(caught.value)


def test_route_matrix_parses_flat_response() -> None:
    cells = [
        {"originIndex": 0, "destinationIndex": 0, "distanceInMeters": 0, "durationInSeconds": 0},
        {
            "originIndex": 0,
            "destinationIndex": 1,
            "distanceInMeters": 1200,
            "durationInSeconds": 180,
        },
        {
            "originIndex": 1,
            "destinationIndex": 0,
            "distanceInMeters": 1250,
            "durationInSeconds": 190,
        },
        {"originIndex": 1, "destinationIndex": 1, "distanceInMeters": 0, "durationInSeconds": 0},
    ]
    session = FakeSession([FakeResponse({"properties": {"matrix": cells}})])
    client = AzureMapsClient("https://example.test", "secret")
    client.session = session

    durations, distances = client.route_matrix([(49.18, -0.37), (49.20, -0.30)])

    assert durations[0][1] == 180
    assert distances[1][0] == 1250
    body = session.calls[0][2]["json"]
    assert body["features"][0]["geometry"]["coordinates"][0] == [-0.37, 49.18]


def test_route_path_parses_first_2025_alternative() -> None:
    payload = {
        "type": "FeatureCollection",
        "alternativeRoutes": [
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "MultiLineString",
                            "coordinates": [
                                [[-0.37, 49.18], [-0.35, 49.19]],
                                [[-0.35, 49.19], [-0.32, 49.20]],
                            ],
                        },
                    }
                ],
            }
        ],
    }
    session = FakeSession([FakeResponse(payload)])
    client = AzureMapsClient("https://example.test", "secret")
    client.session = session

    path = client.route_path([(49.18, -0.37), (49.20, -0.32)])

    assert path == [(49.18, -0.37), (49.19, -0.35), (49.20, -0.32)]
    assert session.calls[0][1] == "https://example.test/route/directions"


def test_static_route_map_requests_only_a_safe_basemap_and_overlays_route_locally() -> None:
    source = _solid_png()
    session = FakeSession([FakeResponse(None, content=source)])
    client = AzureMapsClient("https://example.test", "secret")
    client.session = session

    image = client.static_route_map(
        [(49.18, -0.37), (49.20, -0.32), (49.18, -0.37)],
        [(49.18, -0.37), (49.19, -0.35), (49.20, -0.32), (49.18, -0.37)],
        return_to_start=True,
        stop_labels=["D · Départ | O'Connor <script>", "1 · Client A; {}", "2 · Client B"],
    )

    assert image.startswith(b"\x89PNG")
    assert session.calls[0][1] == "https://example.test/map/static"
    params = _params_as_dict(session.calls[0][2]["params"])
    assert set(params) == {
        "api-version",
        "tilesetId",
        "center",
        "zoom",
        "width",
        "height",
        "language",
    }
    assert params["api-version"] == "2024-04-01"
    assert params["tilesetId"] == "microsoft.base.road"
    assert params["center"] == "-0.345000,49.190000"
    assert params["width"] == 1200
    assert params["height"] == 650
    assert session.calls[0][2]["headers"]["Accept"] == "image/png"

    # No customer-controlled text or Azure pin/path grammar reaches the request.
    request_text = str(session.calls[0][2]["params"])
    assert "path" not in params
    assert "pins" not in params
    assert "O'Connor" not in request_text
    assert "<script>" not in request_text
    assert "Client A" not in request_text

    # The returned image retains the Azure basemap and contains a locally drawn route/markers.
    with PillowImage.open(io.BytesIO(image)) as rendered:
        rendered.load()
        assert rendered.size == (1200, 650)
        pixels = list(rendered.convert("RGB").get_flattened_data())
    assert rendered.format == "PNG"
    assert pixels.count((49, 83, 109)) > len(pixels) // 2
    assert any(pixel != (49, 83, 109) for pixel in pixels)


def test_static_route_map_request_stays_small_with_many_stops_and_special_labels() -> None:
    source = _solid_png()
    session = FakeSession([FakeResponse(None, content=source)])
    client = AzureMapsClient("https://example.test", "secret")
    client.session = session

    stops = [(49.10 + index * 0.01, -0.50 + index * 0.01) for index in range(12)]
    geometry = [
        (49.10 + index * 0.001, -0.50 + index * 0.001)
        for index in range(111)
    ]
    labels = [f"{index} · O'Connor | <client-{index}>; ?&%" for index in range(len(stops))]

    image = client.static_route_map(
        stops,
        geometry,
        return_to_start=False,
        stop_labels=labels,
    )

    assert image.startswith(b"\x89PNG")
    params = _params_as_dict(session.calls[0][2]["params"])
    assert "path" not in params
    assert "pins" not in params
    prepared_url = requests.Request(
        "GET", session.calls[0][1], params=session.calls[0][2]["params"]
    ).prepare().url
    assert prepared_url is not None
    assert len(prepared_url) < 500
    assert "O%27Connor" not in prepared_url
    assert "client-" not in prepared_url


def test_static_map_pixel_uses_azure_road_map_tile_scale() -> None:
    # Azure Maps road maps use 512-pixel tiles. At zoom 1, 90° east of the
    # center is one quarter of a 1024-pixel world, i.e. 256 pixels right.
    assert AzureMapsClient._static_map_pixel(
        point=(0.0, 90.0),
        center=(0.0, 0.0),
        zoom=1,
        width=1200,
        height=650,
    ) == (856, 325)


def test_static_map_diagnostic_reports_a_available_png_without_leaking_the_key() -> None:
    session = FakeSession(
        [
            FakeResponse(
                None,
                content=b"\x89PNG\r\n\x1a\nimage",
                headers={
                    "content-type": "image/png",
                    "x-ms-request-id": "azure-request-123",
                },
            )
        ]
    )
    client = AzureMapsClient("https://example.test", "top-secret-key")
    client.session = session

    diagnostic = client.static_map_diagnostic()

    assert diagnostic.available is True
    assert diagnostic.status_code == 200
    assert diagnostic.content_type == "image/png"
    assert diagnostic.request_id == "azure-request-123"
    assert diagnostic.response_kind == "png"
    assert "top-secret-key" not in diagnostic.message
    assert session.calls[0][0:2] == ("GET", "https://example.test/map/static")
    assert session.calls[0][2]["headers"]["subscription-key"] == "top-secret-key"
    assert session.calls[0][2]["headers"]["Accept"] == "image/png"
    assert "top-secret-key" not in session.calls[0][1]
    assert "top-secret-key" not in str(session.calls[0][2]["params"])


def test_static_map_diagnostic_sanitizes_a_403_html_response() -> None:
    session = FakeSession(
        [
            FakeResponse(
                None,
                status_code=403,
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "x-ms-request-id": "azure-request-403",
                },
            )
        ]
    )
    session.responses[0].text = "<html>proxy details and top-secret-key</html>"
    client = AzureMapsClient("https://example.test", "top-secret-key")
    client.session = session

    diagnostic = client.static_map_diagnostic()

    assert diagnostic.available is False
    assert diagnostic.status_code == 403
    assert diagnostic.content_type == "text/html"
    assert diagnostic.request_id == "azure-request-403"
    assert diagnostic.response_kind == "html"
    assert "403" in diagnostic.message
    assert "proxy details" not in diagnostic.message
    assert "top-secret-key" not in diagnostic.message
