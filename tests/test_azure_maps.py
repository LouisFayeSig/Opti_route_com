from __future__ import annotations

from typing import Any

import pytest
import requests

from opti_route.azure_maps import AzureMapsClient, AzureMapsError


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        status_code: int = 200,
        content: bytes = b"",
    ):
        self.payload = payload
        self.status_code = status_code
        self.text = ""
        self.content = content

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


def test_static_route_map_uses_current_render_api() -> None:
    session = FakeSession([FakeResponse(None, content=b"\x89PNG\r\n\x1a\nimage")])
    client = AzureMapsClient("https://example.test", "secret")
    client.session = session

    image = client.static_route_map(
        [(49.18, -0.37), (49.20, -0.32), (49.18, -0.37)],
        [(49.18, -0.37), (49.19, -0.35), (49.20, -0.32), (49.18, -0.37)],
        return_to_start=True,
    )

    assert image.startswith(b"\x89PNG")
    assert session.calls[0][1] == "https://example.test/map/static"
    params = session.calls[0][2]["params"]
    assert ("api-version", "2024-04-01") in params
    assert any(name == "path" for name, _ in params)
    assert sum(name == "pins" for name, _ in params) == 2
