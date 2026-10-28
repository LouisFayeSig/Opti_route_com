from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .azure_maps import AzureMapsClient, AzureMapsError
from .geo import clients_within_radius, estimated_road_matrix, haversine_km
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
    radius_km: float
    required_client_ids: frozenset[str] = field(default_factory=frozenset)
    route_extension_count: int = 0
    omitted_for_duration: int = 0
    warnings: list[str] = field(default_factory=list)
    map_image: bytes | None = None
    prefer_nearest_first: bool = False
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

    def review_table(self) -> pd.DataFrame:
        """Retourne le tableau affiché dans la revue de tournée, départ inclus.

        Le retour éventuel est volontairement absent : la revue représente les visites que
        l'utilisateur peut conserver ou retirer, et constitue également la référence du PDF.
        """
        visits = self.table[
            [
                "Ordre",
                "Client",
                "Ville",
                "Adresse",
                "Distance",
                "Temps",
                "Distance cumulée",
                "Temps cumulé",
                "Rendez-vous prévu",
            ]
        ].rename(
            columns={
                "Client": "Entreprise",
                "Rendez-vous prévu": "Rendez-vous planifié",
            }
        )
        start = pd.DataFrame(
            [
                {
                    "Ordre": 0,
                    "Entreprise": f"Départ · {self.start.label}",
                    "Ville": "",
                    "Adresse": self.start.address or self.start.label,
                    "Distance": 0.0,
                    "Temps": 0.0,
                    "Distance cumulée": 0.0,
                    "Temps cumulé": 0.0,
                    "Rendez-vous planifié": False,
                }
            ]
        )
        return pd.concat([start, visits], ignore_index=True)

    def itinerary_table(self) -> pd.DataFrame:
        """Ajoute le départ et, le cas échéant, le retour au détail des visites."""
        rows: list[dict[str, object]] = [
            {
                "Étape": "Départ",
                "Entreprise": self.start.label,
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
                    "Entreprise": visit["Client"],
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
                    "Entreprise": destination.label,
                    "Ville": "",
                    "Adresse": destination.address or destination.label,
                    "Distance": (self.total_distance_m - previous_distance_m) / 1000,
                    "Temps": (self.total_duration_s - previous_duration_s) / 60,
                    "Distance cumulée": self.total_distance_m / 1000,
                    "Temps cumulé": self.total_duration_s / 60,
                }
            )
        return pd.DataFrame(rows)


def _required_route_coordinates(
    required_rows: pd.DataFrame, start: StartPoint
) -> list[tuple[float, float]]:
    """Retourne l'ordre de base départ -> rendez-vous imposés.

    Ce calcul est volontairement léger : il sert seulement à présélectionner les
    entreprises qui prolongent naturellement une tournée imposée. L'optimisation
    finale reste effectuée sur la matrice routière complète dans ``build_route_plan``.
    """

    coordinates = [(start.latitude, start.longitude)] + list(
        zip(
            required_rows["latitude"].astype(float),
            required_rows["longitude"].astype(float),
            strict=True,
        )
    )
    if len(coordinates) <= 1:
        return coordinates

    durations, distances = estimated_road_matrix(coordinates)
    ordered_nodes = optimize_route(
        durations,
        distances,
        objective="time",
        return_to_start=False,
        prefer_nearest_first=True,
    )
    return [coordinates[node] for node in ordered_nodes]


def _minimum_insertion_detour_km(
    route_coordinates: list[tuple[float, float]], candidate: tuple[float, float]
) -> tuple[float, int]:
    """Calcule le détour direct minimal pour insérer un candidat dans une tournée ouverte."""

    best_detour = float("inf")
    best_insertion_index = len(route_coordinates)
    for insertion_index in range(1, len(route_coordinates)):
        origin = route_coordinates[insertion_index - 1]
        destination = route_coordinates[insertion_index]
        detour = (
            haversine_km(*origin, *candidate)
            + haversine_km(*candidate, *destination)
            - haversine_km(*origin, *destination)
        )
        if detour < best_detour:
            best_detour = detour
            best_insertion_index = insertion_index

    last_stop = route_coordinates[-1]
    tail_detour = haversine_km(*last_stop, *candidate)
    if tail_detour < best_detour:
        return tail_detour, len(route_coordinates)
    return best_detour, best_insertion_index


def _select_low_detour_candidates(
    candidates: pd.DataFrame,
    required_rows: pd.DataFrame,
    start: StartPoint,
    count: int,
) -> pd.DataFrame:
    """Ajoute les candidats dont l'insertion détourne le moins la tournée imposée.

    Les distances directes servent à limiter une présélection potentiellement très
    large. Une fois les candidats retenus, la matrice Azure Maps (ou son repli local)
    et OR-Tools calculent le véritable itinéraire routier.
    """

    if count <= 0 or candidates.empty:
        return candidates.iloc[0:0].copy()

    remaining = candidates.dropna(subset=["latitude", "longitude"]).drop_duplicates(
        subset=["client_id"], keep="first"
    ).reset_index(drop=True)
    selected_rows: list[pd.Series] = []
    route_coordinates = _required_route_coordinates(required_rows, start)

    while len(selected_rows) < count and not remaining.empty:
        ranked: list[tuple[float, str, int, int]] = []
        for position, row in remaining.iterrows():
            candidate_coordinates = (float(row["latitude"]), float(row["longitude"]))
            detour, insertion_index = _minimum_insertion_detour_km(
                route_coordinates, candidate_coordinates
            )
            ranked.append((detour, str(row["client_id"]), position, insertion_index))

        _, _, position, insertion_index = min(ranked)
        chosen = remaining.iloc[position]
        selected_rows.append(chosen)
        route_coordinates.insert(
            insertion_index, (float(chosen["latitude"]), float(chosen["longitude"]))
        )
        remaining = remaining.drop(index=position).reset_index(drop=True)

    if not selected_rows:
        return candidates.iloc[0:0].copy()
    return pd.DataFrame(selected_rows).reset_index(drop=True)


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
    required_client_ids: Collection[str] | None = None,
    prefer_nearest_first: bool = False,
) -> RoutePlan:
    if end is not None:
        return_to_start = False
    required_ids = frozenset(str(client_id) for client_id in (required_client_ids or ()) if client_id)
    eligible_clients = clients.copy()
    if excluded_client_id is not None:
        eligible_clients = eligible_clients[
            eligible_clients["client_id"].astype(str) != str(excluded_client_id)
        ]
    required_rows = eligible_clients[
        eligible_clients["client_id"].astype(str).isin(required_ids)
    ].dropna(subset=["latitude", "longitude"])
    required_rows = required_rows.drop_duplicates(subset=["client_id"], keep="first")
    available_required_ids = set(required_rows["client_id"].astype(str))
    missing_required_ids = required_ids.difference(available_required_ids)
    if missing_required_ids:
        raise PlanningError(
            "Un rendez-vous planifié n'a pas de coordonnées exploitables ou correspond au point de départ."
        )
    if len(required_rows) > max_visits:
        raise PlanningError(
            f"{len(required_rows)} rendez-vous planifiés dépassent le maximum de {max_visits} visites."
        )

    candidates = clients_within_radius(
        eligible_clients, start.latitude, start.longitude, radius_km
    )
    candidate_count = len(candidates)
    if candidates.empty and required_rows.empty:
        raise PlanningError(
            f"Aucune entreprise géocodée n'a été trouvée dans un rayon de {radius_km:g} km."
        )

    optional_candidates = candidates[
        ~candidates["client_id"].astype(str).isin(required_ids)
    ]
    required_outside_radius = len(required_rows) - int(
        required_rows["client_id"].astype(str).isin(candidates["client_id"].astype(str)).sum()
    )
    in_radius_selection = optional_candidates.head(max_visits - len(required_rows))
    remaining_slots = max_visits - len(required_rows) - len(in_radius_selection)
    route_extension = eligible_clients.iloc[0:0].copy()
    if remaining_slots > 0 and required_outside_radius:
        selected_ids = set(
            pd.concat([required_rows, in_radius_selection], ignore_index=True)["client_id"]
            .astype(str)
            .tolist()
        )
        outside_radius_candidates = eligible_clients[
            ~eligible_clients["client_id"].astype(str).isin(selected_ids)
            & ~eligible_clients["client_id"].astype(str).isin(candidates["client_id"].astype(str))
        ]
        route_extension = _select_low_detour_candidates(
            outside_radius_candidates,
            required_rows,
            start,
            remaining_slots,
        )
    selected = pd.concat(
        [required_rows, in_radius_selection, route_extension],
        ignore_index=True,
    ).drop_duplicates(subset=["client_id"], keep="first")
    warnings: list[str] = []
    if required_outside_radius:
        warnings.append(
            f"{required_outside_radius} rendez-vous planifié(s) hors du rayon ont été inclus."
        )
    if not route_extension.empty:
        warnings.append(
            f"{len(route_extension)} entreprise(s) hors du rayon ont été ajoutée(s) avec le moindre "
            "détour le long des rendez-vous planifiés."
        )
    if len(selected) < max_visits:
        warnings.append(
            f"Tournée incomplète : {len(selected)} visite(s) trouvée(s) sur {max_visits} demandées "
            "dans les entreprises sélectionnées."
        )

    node_coordinates = [(start.latitude, start.longitude)] + list(
        zip(selected["latitude"].astype(float), selected["longitude"].astype(float))
    )
    end_node: int | None = None
    if end is not None:
        node_coordinates.append((end.latitude, end.longitude))
        end_node = len(node_coordinates) - 1
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
        prefer_nearest_first=prefer_nearest_first,
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
                "Rendez-vous prévu": str(client["client_id"]) in required_ids,
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
        radius_km=radius_km,
        required_client_ids=required_ids,
        route_extension_count=len(route_extension),
        omitted_for_duration=len(selected) - len(visited_nodes),
        warnings=warnings,
        map_image=map_image,
        prefer_nearest_first=prefer_nearest_first,
    )


def rebuild_route_plan(
    plan: RoutePlan,
    retained_visit_positions: list[int],
    azure_client: AzureMapsClient | None = None,
) -> RoutePlan:
    """Recalcule entièrement une tournée après retrait de visites du résultat."""
    positions = sorted(set(retained_visit_positions))
    if not positions:
        raise PlanningError("Conservez au moins une entreprise dans la tournée.")
    if positions[0] < 0 or positions[-1] >= plan.visit_count:
        raise PlanningError("La sélection des visites est invalide.")

    mandatory_positions = [
        position
        for position, client_id in enumerate(plan.table["Code client"].astype(str))
        if client_id in plan.required_client_ids
    ]
    positions = sorted(set(positions).union(mandatory_positions))

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
        prefer_nearest_first=plan.prefer_nearest_first,
    )
