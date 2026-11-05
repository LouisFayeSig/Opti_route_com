from __future__ import annotations

import pandas as pd
import pytest

from opti_route.geocoding_validation import (
    coordinates_match_address,
    expected_country,
    geocoding_query,
    normalize_postal_code,
    sanitize_client_coordinates,
)


@pytest.mark.parametrize(
    ("code", "latitude", "longitude"),
    [
        ("30350", 44.0, 40.0),  # LM Agencement / Lézan : point en Russie.
        ("98000", -25.0, -140.0),  # MC BAT / Monaco : point dans le Pacifique.
        ("14000", 23.0, -60.0),  # Adresse métropolitaine : point aux Caraïbes.
        ("30350", 44.0, float("inf")),
        ("30350", float("nan"), 4.0),
        ("30350", 4.0, 44.0),  # Latitude et longitude inversées.
        ("14000", 14.6, -61.0),  # Point en Martinique pour une adresse métropolitaine.
        ("97400", 48.85, 2.35),  # Point parisien pour une adresse réunionnaise.
    ],
)
def test_rejects_coordinates_inconsistent_with_postcode(code, latitude, longitude) -> None:
    assert not coordinates_match_address(latitude, longitude, postal_code=code)


@pytest.mark.parametrize(
    ("code", "latitude", "longitude"),
    [
        ("30350", 44.0, 4.05),
        ("98000", 43.738, 7.425),
        ("20000", 41.92, 8.74),
        ("97400", -20.88, 55.45),
        ("97200", 14.6, -61.0),
        ("98714", -17.55, -149.57),
    ],
)
def test_accepts_metropolitan_monaco_and_overseas_coordinates(code, latitude, longitude) -> None:
    assert coordinates_match_address(latitude, longitude, postal_code=code)


def test_monaco_in_a_street_name_does_not_override_a_french_postcode() -> None:
    assert coordinates_match_address(
        43.6, 3.9, address="1 avenue de Monaco, 34000 Montpellier, France"
    )


def test_postcode_rule_is_specific_to_the_portfolio_and_preserves_monaco() -> None:
    assert expected_country("30350", "", "Lézan") == "France"
    assert expected_country("98000", "France", "Monaco") == "Monaco"
    assert normalize_postal_code(1000.0) == "01000"
    assert expected_country("39025", "Italie", "Naturno") == "Italie"
    assert normalize_postal_code("3490", "Luxembourg") == "3490"


def test_explicit_foreign_countries_are_validated_before_the_five_digit_rule() -> None:
    assert coordinates_match_address(46.648, 10.9, postal_code="39025", country="Italie")
    assert coordinates_match_address(41.603, -8.398, postal_code="4710-167", country="Portugal")
    assert coordinates_match_address(49.48, 6.08, postal_code="3490", country="Luxembourg")
    assert not coordinates_match_address(14.60, -61.07, postal_code="3490", country="Luxembourg")
    assert coordinates_match_address(
        46.648, 10.9, address="ZONA INDUSTRIALE 7-9, 39025, Naturno, Italie"
    )


def test_legacy_monaco_address_is_not_sent_to_the_geocoder_as_france() -> None:
    assert geocoding_query("9 RUE PLATI, 98012, MONACO CEDEX, France").endswith(
        "MONACO CEDEX, Monaco"
    )


def test_coordinate_sanitization_clears_both_values_without_changing_the_input() -> None:
    clients = pd.DataFrame(
        {
            "postal_code": ["30350", "98000", "14000"],
            "latitude": [44.0, 43.738, 49.18],
            "longitude": [40.0, 7.425, -0.37],
        }
    )

    result = sanitize_client_coordinates(clients)

    assert result.loc[0, ["latitude", "longitude"]].isna().all()
    assert result.loc[1, "longitude"] == 7.425
    assert result.attrs["rejected_coordinate_count"] == 1
    assert clients.loc[0, "longitude"] == 40.0
