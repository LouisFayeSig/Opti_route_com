from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .azure_maps import AzureMapsClient, AzureMapsError
from .geo import clients_within_radius, estimated_road_matrix
from .optimizer import optimize_route


class PlanningError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartPoint:
    latitude: float
    longitude: float
    label: str = "Départ"


@dataclass
class RoutePlan:
    start: StartPoint
    end: StartPoint | None
    table: pd.DataFrame
    route_coordinates: list[tuple[float, float]]
    geometry: list[tuple[float, float]]
    total_distance_m: int
    total_duration_s: int
    return_to_start: bool
    provider: str
    candidates_in_radius: int
    omitted_for_duration: int = 0
    warnings: list[str] = field(default_factory=list)
    map_image: bytes | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @property
    def visit_count(self) -> int:
        return len(self.table)

    @property
    def export_stem(self) -> str:
        return f"tournee_commerciale_{self.created_at:%Y%m%d_%H%M%S}"

    def itinerary_table(self) -> pd.DataFrame:
        """Ajoute le départ et, le cas échéant, le retour au détail des visites."""
        rows: list[dict[str, object]] = [
            {
                "Étape": "Départ",
                "Client": self.start.label,
                "Ville": "",
                "Adresse": self.start.label,
                "Distance": 0.0,
                "Temps": 0.0,
                "Distance cumulée": 0.0,
                "Temps cumulé": 0.0,
            }
        ]
        for _, visit in self.table.iterrows():
            rows.append(
                {
                    "Étape": str(int(visit["Ordre"])),
                    "Client": visit["Client"],
                    "Ville": visit["Ville"],
                    "Adresse": visit["Adresse"],
                    "Distance": visit["Distance"],
                    "Temps": visit["Temps"],
                    "Distance cumulée": visit["Distance cumulée"],
                    "Temps cumulé": visit["Temps cumulé"],
                }
            )
        if self.return_to_start or self.end is not None:
            previous_distance_m = round(float(self.table.iloc[-1]["Distance cumulée"]) * 1000)
            previous_duration_s = round(float(self.table.iloc[-1]["Temps cumulé"]) * 60)
            is_return = self.return_to_start
            destination = self.start if is_return else self.end
            assert destination is not None
            rows.append(
                {
                    "Étape": "Retour" if is_return else "Arrivée",
                    "Client": destination.label,
                    "Ville": "",
                    "Adresse": destination.label,
                    "Distance": (self.total_distance_m - previous_distance_m) / 1000,
                    "Temps": (self.total_duration_s - previous_duration_s) / 60,
                    "Distance cumulée": self.total_distance_m / 1000,
                    "Temps cumulé": self.total_duration_s / 60,
                }
            )
        return pd.DataFrame(rows)


def build_route_plan(
    clients: pd.DataFrame,
    start: StartPoint,
    radius_km: float,
    max_visits: int,
    max_duration_hours: float | None,
    return_to_start: bool,
    objective: str,
    azure_client: AzureMapsClient | None = None,
    excluded_client_id: str | None = None,
    end: StartPoint | None = None,
) -> RoutePlan:
    if end is not None:
        return_to_start = False
    candidates = clients_within_radius(clients, start.latitude, start.longitude, radius_km)
    if excluded_client_id is not None:
        candidates = candidates[candidates["client_id"].astype(str) != str(excluded_client_id)]
    candidate_count = len(candidates)
    if candidates.empty:
        raise PlanningError(
            f"Aucun client géocodé n'a été trouvé dans un rayon de {radius_km:g} km."
        )

    selected = candidates.head(max_visits).reset_index(drop=True)
    node_coordinates = [(start.latitude, start.longitude)] + list(
        zip(selected["latitude"].astype(float), selected["longitude"].astype(float))
    )
    end_node: int | None = None
    if end is not None:
        node_coordinates.append((end.latitude, end.longitude))
        end_node = len(node_coordinates) - 1
    warnings: list[str] = []
    provider = "Estimation géodésique"
    if azure_client is not None:
        try:
            durations, distances = azure_client.route_matrix(node_coordinates)
            provider = "Azure Maps"
        except AzureMapsError as exc:
            warnings.append(f"Matrice Azure indisponible : {exc} Estimation locale utilisée.")
            durations, distances = estimated_road_matrix(node_coordinates)
    else:
        durations, distances = estimated_road_matrix(node_coordinates)
        warnings.append(
            "Clé Azure Maps absente : distances à vol d'oiseau corrigées et vitesse moyenne de 45 km/h."
        )

    ordered_nodes = optimize_route(
        durations,
        distances,
        objective=objective,
        return_to_start=return_to_start,
        max_duration_seconds=(
            round(max_duration_hours * 3600) if max_duration_hours is not None else None
        ),
        end_node=end_node,
    )
    visited_nodes = [node for node in ordered_nodes if node != 0 and node != end_node]
    if not visited_nodes:
        raise PlanningError("Aucune visite n'a pu être intégrée à la tournée.")

    rows: list[dict[str, object]] = []
    cumulative_distance = 0
    cumulative_duration = 0
    previous = 0
    for order, node in enumerate(visited_nodes, start=1):
        client = selected.iloc[node - 1]
        leg_distance = distances[previous][node]
        leg_duration = durations[previous][node]
        cumulative_distance += leg_distance
        cumulative_duration += leg_duration
        rows.append(
            {
                "Ordre": order,
                "Client": client["client_name"],
                "Ville": client.get("city", ""),
                "Adresse": client.get("full_address", ""),
                "Distance": leg_distance / 1000,
                "Temps": leg_duration / 60,
                "Distance cumulée": cumulative_distance / 1000,
                "Temps cumulé": cumulative_duration / 60,
                "Latitude": float(client["latitude"]),
                "Longitude": float(client["longitude"]),
                "Code client": client["client_id"],
            }
        )
        previous = node

    if return_to_start:
        cumulative_distance += distances[previous][0]
        cumulative_duration += durations[previous][0]
    elif end_node is not None:
        cumulative_distance += distances[previous][end_node]
        cumulative_duration += durations[previous][end_node]

    route_coordinates = [node_coordinates[node] for node in ordered_nodes]
    geometry = route_coordinates
    map_image: bytes | None = None
    if azure_client is not None and provider == "Azure Maps":
        try:
            geometry = azure_client.route_path(route_coordinates)
        except AzureMapsError as exc:
            warnings.append(f"Tracé routier Azure indisponible : {exc}")
        try:
            map_image = azure_client.static_route_map(
                route_coordinates,
                geometry,
                return_to_start=return_to_start,
            )
        except AzureMapsError as exc:
            warnings.append(f"Capture Azure indisponible pour le PDF : {exc}")

    return RoutePlan(
        start=start,
        end=end,
        table=pd.DataFrame(rows),
        route_coordinates=route_coordinates,
        geometry=geometry,
        total_distance_m=cumulative_distance,
        total_duration_s=cumulative_duration,
        return_to_start=return_to_start,
        provider=provider,
        candidates_in_radius=candidate_count,
        omitted_for_duration=len(selected) - len(visited_nodes),
        warnings=warnings,
        map_image=map_image,
    )
