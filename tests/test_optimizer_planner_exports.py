from __future__ import annotations

import re

import pandas as pd

from opti_route.exporting import csv_bytes, excel_bytes, google_maps_url, pdf_bytes
from opti_route.optimizer import optimize_route
from opti_route.planner import StartPoint, build_route_plan


def test_optimizer_visits_each_node_and_returns() -> None:
    matrix = [
        [0, 10, 20, 30],
        [10, 0, 10, 20],
        [20, 10, 0, 10],
        [30, 20, 10, 0],
    ]
    route = optimize_route(matrix, matrix, return_to_start=True, time_limit_seconds=1)
    assert route[0] == route[-1] == 0
    assert sorted(route[1:-1]) == [1, 2, 3]


def test_planner_builds_route_and_exports() -> None:
    clients = pd.DataFrame(
        {
            "client_id": ["A", "B", "C"],
            "client_name": ["Alpha", "Beta", "Gamma"],
            "salesperson": ["Morgan"] * 3,
            "address": ["Adresse A", "Adresse B", "Adresse C"],
            "address_2": [pd.NA] * 3,
            "address_3": [pd.NA] * 3,
            "postal_code": ["14000", "14120", "14200"],
            "city": ["Caen", "Mondeville", "Hérouville-Saint-Clair"],
            "country": ["France"] * 3,
            "latitude": [49.183, 49.174, 49.205],
            "longitude": [-0.370, -0.320, -0.335],
            "full_address": ["A", "B", "C"],
        }
    )
    plan = build_route_plan(
        clients,
        StartPoint(49.1829, -0.3707, "Caen"),
        radius_km=20,
        max_visits=3,
        max_duration_hours=4,
        return_to_start=True,
        objective="time",
    )

    assert plan.visit_count == 3
    assert plan.route_coordinates[0] == plan.route_coordinates[-1]
    assert plan.total_distance_m > 0
    assert plan.itinerary_table()["Étape"].tolist() == ["Départ", "1", "2", "3", "Retour"]
    assert csv_bytes(plan).startswith(b"\xef\xbb\xbf")
    assert "Départ" in csv_bytes(plan).decode("utf-8-sig")
    assert excel_bytes(plan).startswith(b"PK")
    pdf = pdf_bytes(plan)
    assert pdf.startswith(b"%PDF")
    assert b"/Subtype /Image" in pdf
    assert re.fullmatch(r"tournee_commerciale_\d{8}_\d{6}", plan.export_stem)
    assert "google.com/maps/dir" in google_maps_url(plan)
