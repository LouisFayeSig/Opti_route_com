from __future__ import annotations

from collections.abc import Sequence
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
    address: str | None = None


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
    required_client_ids: frozenset[str] = field(default_factory=frozenset)
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

    @property
    def map_stop_labels(self) -> list[str]:
        labels = [f"D · {self.start.label}"]
        labels.extend(f"{int(row['Ordre'])} · {row['Client']}" for _, row in self.table.iterrows())
        if self.end is not None:
            labels.append(f"A · {self.end.label}")
        return labels

    def itinerary_table(self) -> pd.DataFrame:
        """Ajoute le départ et, le cas échéant, le retour au détail des visites."""
        rows: list[dict[str, object]] = [
            {
                "Étape": "Départ",
                "Type": "",
                "Client": self.start.label,
                "Ville": "",
                "Adresse": self.start.address or self.start.label,
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
                    "Type": visit.get("Type", "Client"),
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
                    "Type": "",
                    "Client": destination.label,
                    "Ville": "",
                    "Adresse": destination.address or destination.label,
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
    required_client_ids: Sequence[str] = (),
    end: StartPoint | None = None,
) -> RoutePlan:
    if end is not None:
        return_to_start = False
    required_ids = frozenset(str(client_id) for client_id in required_client_ids if client_id)
    eligible_clients = clients.copy()
    if excluded_client_id is not None:
        eligible_clients = eligible_clients[
            eligible_clients["client_id"].astype(str) != str(excluded_client_id)
        ]
    required = eligible_clients[
        eligible_clients["client_id"].astype(str).isin(required_ids)
    ].dropna(subset=["latitude", "longitude"])
    required = required.drop_duplicates(subset=["client_id"], keep="first")
    missing_required_ids = required_ids.difference(required["client_id"].astype(str))
    if missing_required_ids:
        raise PlanningError(
            "Un rendez-vous planifié n'a pas de coordonnées exploitables ou correspond au point de départ."
        )
    if len(required) > max_visits:
        raise PlanningError(
            f"{len(required)} rendez-vous planifiés dépassent le maximum de {max_visits} entreprises à visiter."
        )

    candidates = clients_within_radius(
        eligible_clients, start.latitude, start.longitude, radius_km
    )
    candidate_count = len(candidates)
    if candidates.empty and required.empty:
        raise PlanningError(
            f"Aucun client géocodé n'a été trouvé dans un rayon de {radius_km:g} km."
        )

    optional = candidates[~candidates["client_id"].astype(str).isin(required_ids)]
    selected = pd.concat(
        [required, optional.head(max_visits - len(required))], ignore_index=True
    ).drop_duplicates(subset=["client_id"], keep="first")
    node_coordinates = [(start.latitude, start.longitude)] + list(
        zip(selected["latitude"].astype(float), selected["longitude"].astype(float))
    )
    end_node: int | None = None
    if end is not None:
        node_coordinates.append((end.latitude, end.longitude))
        end_node = len(node_coordinates) - 1
    warnings: list[str] = []
    required_outside_radius = len(required) - int(
        required["client_id"].astype(str).isin(candidates["client_id"].astype(str)).sum()
    )
    if required_outside_radius:
        warnings.append(
            f"{required_outside_radius} rendez-vous planifié(s) hors du rayon ont été inclus."
        )
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
                "Type": "Client",
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
            static_labels = [f"D · {start.label}"]
            static_labels.extend(f"{row['Ordre']} · {row['Client']}" for row in rows)
            if end is not None:
                static_labels.append(f"A · {end.label}")
            map_image = azure_client.static_route_map(
                route_coordinates,
                geometry,
                return_to_start=return_to_start,
                stop_labels=static_labels,
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
        required_client_ids=required_ids,
        omitted_for_duration=len(selected) - len(visited_nodes),
        warnings=warnings,
        map_image=map_image,
    )


def rebuild_route_plan(
    plan: RoutePlan,
    retained_visit_positions: list[int],
    azure_client: AzureMapsClient | None = None,
) -> RoutePlan:
    """Recalcule entièrement une tournée après retrait de visites du résultat."""
    positions = sorted(set(retained_visit_positions))
    if positions and (positions[0] < 0 or positions[-1] >= plan.visit_count):
        raise PlanningError("La sélection des visites est invalide.")

    mandatory_positions = [
        position
        for position, client_id in enumerate(plan.table["Code client"].astype(str))
        if client_id in plan.required_client_ids
    ]
    positions = sorted(set(positions).union(mandatory_positions))
    if not positions:
        raise PlanningError("Conservez au moins une entreprise dans la tournée.")

    visits = plan.table.iloc[positions]
    clients = pd.DataFrame(
        {
            "client_id": visits["Code client"].to_numpy(),
            "client_name": visits["Client"].to_numpy(),
            "city": visits["Ville"].to_numpy(),
            "full_address": visits["Adresse"].to_numpy(),
            "latitude": visits["Latitude"].astype(float).to_numpy(),
            "longitude": visits["Longitude"].astype(float).to_numpy(),
        }
    )
    # Le rayon terrestre maximal permet de conserver exactement les lignes choisies ;
    # la présélection par rayon a déjà eu lieu lors de la génération initiale.
    return build_route_plan(
        clients,
        plan.start,
        radius_km=21_000,
        max_visits=len(clients),
        max_duration_hours=None,
        return_to_start=plan.return_to_start,
        objective="time",
        azure_client=azure_client,
        end=plan.end,
        required_client_ids=plan.required_client_ids,
    )
