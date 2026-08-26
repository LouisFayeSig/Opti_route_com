from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise


class OptimizationError(RuntimeError):
    pass


def _route_duration(route: Sequence[int], durations: Sequence[Sequence[int]]) -> int:
    return sum(durations[origin][destination] for origin, destination in pairwise(route))


def _greedy_route(
    costs: Sequence[Sequence[int]],
    durations: Sequence[Sequence[int]],
    return_to_start: bool,
    max_duration_seconds: int | None,
) -> list[int]:
    remaining = set(range(1, len(costs)))
    route = [0]
    while remaining:
        next_node = min(remaining, key=lambda node: costs[route[-1]][node])
        proposed = [*route, next_node]
        if return_to_start:
            proposed.append(0)
        if max_duration_seconds is not None and _route_duration(proposed, durations) > max_duration_seconds:
            remaining.remove(next_node)
            continue
        route.append(next_node)
        remaining.remove(next_node)
    if return_to_start:
        route.append(0)
    return route


def optimize_route(
    durations: Sequence[Sequence[int]],
    distances: Sequence[Sequence[int]],
    objective: str = "time",
    return_to_start: bool = True,
    max_duration_seconds: int | None = None,
    time_limit_seconds: int = 2,
) -> list[int]:
    if len(durations) != len(distances) or not durations:
        raise OptimizationError("Les matrices de tournée sont invalides.")
    if len(durations) == 1:
        return [0, 0] if return_to_start else [0]

    costs = durations if objective == "time" else distances
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError:
        return _greedy_route(
            costs, durations, return_to_start=return_to_start, max_duration_seconds=max_duration_seconds
        )

    size = len(costs)
    manager = pywrapcp.RoutingIndexManager(size, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def cost_callback(from_index: int, to_index: int) -> int:
        origin = manager.IndexToNode(from_index)
        destination = manager.IndexToNode(to_index)
        if not return_to_start and destination == 0:
            return 0
        return int(costs[origin][destination])

    cost_index = routing.RegisterTransitCallback(cost_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_index)

    def duration_callback(from_index: int, to_index: int) -> int:
        origin = manager.IndexToNode(from_index)
        destination = manager.IndexToNode(to_index)
        if not return_to_start and destination == 0:
            return 0
        return int(durations[origin][destination])

    duration_index = routing.RegisterTransitCallback(duration_callback)
    maximum = max_duration_seconds or max(1, sum(max(row) for row in durations))
    routing.AddDimension(duration_index, 0, int(maximum), True, "TravelTime")

    # Une pénalité élevée conserve tous les clients tant que la durée maximale le permet.
    max_cost = max(max(row) for row in costs)
    penalty = max(1, max_cost * size * 10)
    for node in range(1, size):
        routing.AddDisjunction([manager.NodeToIndex(node)], penalty)

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.seconds = time_limit_seconds
    solution = routing.SolveWithParameters(parameters)
    if solution is None:
        raise OptimizationError("OR-Tools n'a trouvé aucune tournée respectant les contraintes.")

    route = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        route.append(manager.IndexToNode(index))
        index = solution.Value(routing.NextVar(index))
    if return_to_start:
        route.append(0)
    return route
