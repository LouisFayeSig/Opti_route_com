from __future__ import annotations

import json

import pandas as pd

from opti_route.map_view import (
    _map_points,
    _persistent_label_layer,
    _points_view,
    portfolio_points_from_clients,
    possibility_points_from_candidates,
)
from opti_route.planner import StartPoint, build_route_plan


def test_portfolio_points_use_only_valid_existing_coordinates() -> None:
    clients = pd.DataFrame(
        {
            "client_name": ["Valide", "Absent", "Texte", "Infini", "Hors limites"],
            "latitude": ["49.18", None, "invalide", float("inf"), 95],
            "longitude": ["-0.37", -0.37, -0.37, -0.37, -0.37],
        }
    )

    points = portfolio_points_from_clients(clients)

    assert len(points) == 1
    assert points[0]["label"] == "Valide"
    assert points[0]["latitude"] == 49.18
    assert points[0]["longitude"] == -0.37
    assert clients.iloc[0]["latitude"] == "49.18"
    assert portfolio_points_from_clients(clients.iloc[:0]) == []


def test_portfolio_view_centers_bounds_even_when_most_clients_are_clustered() -> None:
    view = _points_view(
        [{"latitude": 48, "longitude": 0}] * 100
        + [{"latitude": 50, "longitude": 10}]
    )

    assert view.latitude == 49
    assert view.longitude == 5


def test_persistent_labels_use_literal_alignment_and_visible_background() -> None:
    points = [
        {
            "latitude": 49.18,
            "longitude": -0.37,
            "map_label": "1. Entreprise Démo",
            "label_offset": [18, -14],
            "label_anchor": "start",
            "label_baseline": "bottom",
        }
    ]

    serialized = json.loads(_persistent_label_layer(points).to_json())

    assert serialized["getText"] == "@@=map_label"
    assert serialized["getAlignmentBaseline"] == "@@=label_baseline"
    assert serialized["getTextAnchor"] == "@@=label_anchor"
    assert serialized["getPixelOffset"] == "@@=label_offset"
    assert serialized["getSize"] == 11
    assert serialized["background"] is True
    assert serialized["characterSet"] == "auto"


def test_admin_possibility_overlay_classifies_companies_from_the_start() -> None:
    route_clients = pd.DataFrame(
        {
            "client_id": ["ROUTE_NEAR", "ROUTE_FAR"],
            "client_name": ["Tournée proche", "Tournée éloignée"],
            "city": ["Caen", "Caen"],
            "full_address": ["A", "B"],
            "latitude": [49.010, 49.030],
            "longitude": [-0.370, -0.370],
        }
    )
    plan = build_route_plan(
        route_clients,
        StartPoint(49.000, -0.370, "Départ"),
        radius_km=20,
        max_visits=2,
        max_duration_hours=None,
        return_to_start=False,
        objective="time",
    )
    all_companies = pd.concat(
        [
            pd.DataFrame(
                {
                    "client_id": ["START", "POSSIBLE", "FAR"],
                    "client_name": ["Départ", "Possibilité", "Plus loin"],
                    "latitude": [49.000, 49.020, 49.250],
                    "longitude": [-0.370, -0.370, -0.370],
                }
            ),
            route_clients,
        ],
        ignore_index=True,
    )

    points, nearby_count, distant_count, configured_radius_km = possibility_points_from_candidates(
        plan, all_companies, "START"
    )

    assert configured_radius_km == 20
    assert nearby_count == distant_count == 1
    assert {point["label"] for point in points} == {"Possibilité", "Plus loin"}
    assert {point["color"] for point in points} == {"#FBBF24", "#22C55E"}
    assert {point["color"] for point in _map_points(plan)[1:]} == {"#D32F2F"}
    for candidates in (all_companies.iloc[:0], route_clients):
        assert possibility_points_from_candidates(plan, candidates, "START") == ([], 0, 0, 20)
