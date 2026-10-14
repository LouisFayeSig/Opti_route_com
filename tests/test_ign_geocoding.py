from __future__ import annotations

from typing import Any

import pytest
import requests

from opti_route.ign_geocoding import IgnGeocodingClient, IgnGeocodingError


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP error", response=self)

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_geocoding_uses_ign_ban_endpoint_and_lon_lat_order() -> None:
    client = IgnGeocodingClient("https://example.test/geocodage/search", max_requests_per_second=40)
    session = FakeSession(
        [
            FakeResponse(
                {
                    "features": [
                        {
                            "geometry": {"type": "Point", "coordinates": [-0.3707, 49.1829]},
                            "properties": {"label": "1 Rue du Test, 14000 Caen"},
                        }
                    ]
                }
            )
        ]
    )
    client.session = session

    result = client.geocode("1 rue du test, 14000 Caen")

    assert result.latitude == 49.1829
    assert result.longitude == -0.3707
    assert result.formatted_address == "1 Rue du Test, 14000 Caen"
    assert session.calls[0][0] == "https://example.test/geocodage/search"
    assert session.calls[0][1]["params"] == {
        "q": "1 rue du test, 14000 Caen",
        "limit": 1,
        "index": "address",
    }


def test_geocoding_reports_no_match_without_exposing_response() -> None:
    client = IgnGeocodingClient("https://example.test/geocodage/search")
    client.session = FakeSession([FakeResponse({"features": []})])

    with pytest.raises(IgnGeocodingError, match="Aucun resultat IGN/BAN"):
        client.geocode("Adresse inconnue")


def test_geocoding_sanitizes_proxy_sensitive_characters() -> None:
    assert (
        IgnGeocodingClient._safe_query("10 RUE D'ATALENTE, 'LE CLOS', France")
        == "10 RUE D ATALENTE, LE CLOS , France"
    )


def test_ign_rate_limit_must_not_exceed_public_limit() -> None:
    with pytest.raises(ValueError, match="entre 0 et 50"):
        IgnGeocodingClient(max_requests_per_second=51)
