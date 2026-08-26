from __future__ import annotations

from typing import Any

from opti_route.azure_maps import AzureMapsClient


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
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
        return self.responses.pop(0)


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


def test_route_matrix_parses_flat_response() -> None:
    cells = [
        {"originIndex": 0, "destinationIndex": 0, "distanceInMeters": 0, "durationInSeconds": 0},
        {"originIndex": 0, "destinationIndex": 1, "distanceInMeters": 1200, "durationInSeconds": 180},
        {"originIndex": 1, "destinationIndex": 0, "distanceInMeters": 1250, "durationInSeconds": 190},
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
