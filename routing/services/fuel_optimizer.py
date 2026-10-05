"""
Route-constrained fuel optimization.

Problem
-------
A vehicle drives a fixed route of length D miles. It has tank capacity
C = max_range / mpg gallons, starts with a full tank (free fuel), and may buy
any amount of fuel (up to capacity) at stations located along the route.
Minimise the total money spent on fuel while never running dry.

Algorithm (fixed-capacity "gas station" greedy)
------------------------------------------------
Standing at a node with price p (the origin counts as price 0 because its
fuel is already in the tank and cannot be topped up):

1. Look at every station within one tank of range ahead.
2. If one of them is strictly cheaper than p, drive to the *nearest* cheaper
   one, buying only enough fuel to get there (often nothing).
3. Otherwise, if the destination is in range, buy only enough to reach it.
4. Otherwise, fill the tank (current fuel is the cheapest available) and
   drive to the cheapest station in range.

Each mile of the route is therefore fuelled by the cheapest fuel that could
legally be in the tank at that mile, which is a lower bound on any feasible
plan, so the greedy is optimal. The test-suite cross-checks it against an
exhaustive fuel-state dynamic program.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from django.conf import settings

from routing.models import FuelStation
from routing.services.geo_utils import RouteSpatialIndex

logger = logging.getLogger(__name__)

POSITION_EPSILON_MILES = 1e-6
MIN_PURCHASE_GALLONS = 1e-6
CANDIDATE_BBOX_BUFFER_DEG = 0.5
SPATIAL_INDEX_CELL_DEG = 0.25


class FuelOptimizationError(Exception):
    """Base exception for fuel optimization failures."""


class NoFeasibleFuelPlanError(FuelOptimizationError):
    """Raised when a route cannot be completed due to unbridgeable range gaps."""


@dataclass(frozen=True)
class CandidateStation:
    """A fuel station projected onto the route."""

    position_miles: float
    price: float
    payload: Any = None


@dataclass(frozen=True)
class FuelPurchase:
    """Fuel bought at a single station."""

    station: CandidateStation
    fuel_on_arrival_gallons: float
    gallons: float

    @property
    def cost(self) -> float:
        return self.gallons * self.station.price

    @property
    def fuel_after_purchase_gallons(self) -> float:
        return self.fuel_on_arrival_gallons + self.gallons


@dataclass
class FuelPlan:
    """Result of the optimisation, with exact (unrounded) quantities."""

    purchases: List[FuelPurchase] = field(default_factory=list)
    starting_fuel_gallons: float = 0.0
    ending_fuel_gallons: float = 0.0
    total_consumed_gallons: float = 0.0

    @property
    def total_purchased_gallons(self) -> float:
        return sum(p.gallons for p in self.purchases)

    @property
    def total_cost(self) -> float:
        return sum(p.cost for p in self.purchases)


def compute_optimal_fuel_plan(
    candidates: Sequence[CandidateStation],
    total_distance_miles: float,
    max_range_miles: float,
    mpg: float,
) -> FuelPlan:
    """
    Compute the minimum-cost purchase plan for a vehicle that starts full.

    Pure function (no DB / network) so it can be tested exhaustively.

    Raises:
        NoFeasibleFuelPlanError: if some gap is longer than one tank of range.
    """
    capacity = max_range_miles / mpg
    stations = sorted(
        (c for c in candidates if 0.0 < c.position_miles < total_distance_miles),
        key=lambda c: c.position_miles,
    )
    plan = FuelPlan(
        starting_fuel_gallons=capacity,
        total_consumed_gallons=total_distance_miles / mpg,
    )

    fuel = capacity
    position = 0.0
    current_price = 0.0  # origin fuel is already paid for
    current_idx = -1  # -1 == origin (cannot buy fuel there)

    while True:
        reach_limit = position + max_range_miles + POSITION_EPSILON_MILES
        nearest_cheaper: Optional[int] = None
        cheapest_in_range: Optional[int] = None

        j = current_idx + 1
        while j < len(stations) and stations[j].position_miles <= reach_limit:
            price_j = stations[j].price
            if price_j < current_price:
                nearest_cheaper = j
                break
            if cheapest_in_range is None or price_j <= stations[cheapest_in_range].price:
                cheapest_in_range = j  # ties -> farther station
            j += 1

        destination_in_range = total_distance_miles <= reach_limit

        if nearest_cheaper is not None:
            target_idx, target_pos, fill_up = nearest_cheaper, stations[nearest_cheaper].position_miles, False
        elif destination_in_range:
            target_idx, target_pos, fill_up = None, total_distance_miles, False
        elif cheapest_in_range is not None:
            target_idx, target_pos, fill_up = cheapest_in_range, stations[cheapest_in_range].position_miles, True
        else:
            raise NoFeasibleFuelPlanError(
                f"Cannot complete route: no fuel station within {max_range_miles:.0f} miles "
                f"after mile {position:.1f} of {total_distance_miles:.1f}."
            )

        leg_gallons = (target_pos - position) / mpg
        if current_idx >= 0:
            needed = capacity - fuel if fill_up else max(0.0, leg_gallons - fuel)
            if needed > MIN_PURCHASE_GALLONS:
                plan.purchases.append(
                    FuelPurchase(
                        station=stations[current_idx],
                        fuel_on_arrival_gallons=fuel,
                        gallons=needed,
                    )
                )
                fuel += needed

        fuel = max(0.0, fuel - leg_gallons)
        position = target_pos

        if target_idx is None:
            plan.ending_fuel_gallons = fuel
            return plan

        current_idx = target_idx
        current_price = stations[target_idx].price


class FuelOptimizer:
    """
    Builds the fuel plan for a routed trip.

    Vehicle assumptions (configurable via settings):
        - Maximum range: 500 miles, 10 MPG -> 50 gallon tank
        - Departs with a full tank (no purchase cost for starting fuel)
        - Buys any amount at a stop (partial fill or fill-up), never above capacity
        - No purchase at the destination
    """

    def __init__(
        self,
        max_range_miles: Optional[float] = None,
        mpg: Optional[float] = None,
        corridor_radius_miles: Optional[float] = None,
    ):
        self.max_range_miles = max_range_miles or getattr(settings, "VEHICLE_MAX_RANGE_MILES", 500.0)
        self.mpg = mpg or getattr(settings, "VEHICLE_MPG", 10.0)
        self.tank_capacity = self.max_range_miles / self.mpg
        self.corridor_radius_miles = corridor_radius_miles or getattr(
            settings, "CORRIDOR_SEARCH_RADIUS_MILES", 10.0
        )

    def optimize(self, route_geometry: Dict[str, Any], total_distance_miles: float) -> Dict[str, Any]:
        """Return the serialisable fuel plan for the given route."""
        coordinates = route_geometry.get("coordinates", [])
        if not coordinates or len(coordinates) < 2:
            raise FuelOptimizationError("Invalid route geometry provided.")

        if total_distance_miles <= self.max_range_miles:
            plan = FuelPlan(
                starting_fuel_gallons=self.tank_capacity,
                ending_fuel_gallons=self.tank_capacity - total_distance_miles / self.mpg,
                total_consumed_gallons=total_distance_miles / self.mpg,
            )
            note = (
                f"Route distance ({total_distance_miles:.1f} mi) is within the vehicle's single-tank "
                f"range ({self.max_range_miles:.0f} mi). Departing with a full tank "
                f"({self.tank_capacity:.0f} gal), zero refueling stops are required."
            )
            return self._serialize(plan, total_distance_miles, note)

        candidates = self._find_candidate_stations(coordinates, total_distance_miles)
        if not candidates:
            raise NoFeasibleFuelPlanError(
                f"No fuel stations found within {self.corridor_radius_miles} miles of the route corridor."
            )

        plan = compute_optimal_fuel_plan(candidates, total_distance_miles, self.max_range_miles, self.mpg)
        note = (
            f"Vehicle departs with a full {self.tank_capacity:.0f}-gallon tank (not charged). "
            f"At each stop it buys only what is cost-optimal: a fill-up when no cheaper fuel is "
            f"reachable, otherwise just enough to reach the next cheaper station or the destination. "
            f"No fuel is purchased at the destination."
        )
        return self._serialize(plan, total_distance_miles, note)

    def _find_candidate_stations(
        self, coordinates: List[List[float]], total_distance_miles: float
    ) -> List[CandidateStation]:
        """
        Bounding-box DB query, then project each station onto the route with
        the spatial index and keep those inside the corridor.

        Along-route positions are measured on the geometry and rescaled to the
        provider's road distance so stations and destination share one scale.
        """
        spatial_index = RouteSpatialIndex(coordinates, cell_size_deg=SPATIAL_INDEX_CELL_DEG)
        geometry_length = spatial_index.cum_distances[-1]
        scale = total_distance_miles / geometry_length if geometry_length > 0 else 1.0

        lons = [c[0] for c in coordinates]
        lats = [c[1] for c in coordinates]
        stations = FuelStation.objects.filter(
            latitude__gte=min(lats) - CANDIDATE_BBOX_BUFFER_DEG,
            latitude__lte=max(lats) + CANDIDATE_BBOX_BUFFER_DEG,
            longitude__gte=min(lons) - CANDIDATE_BBOX_BUFFER_DEG,
            longitude__lte=max(lons) + CANDIDATE_BBOX_BUFFER_DEG,
        ).only(
            "id", "opis_id", "name", "address", "city", "state",
            "retail_price", "latitude", "longitude",
        )

        candidates: List[CandidateStation] = []
        for station in stations:
            along, off_route = spatial_index.project_station(
                station.latitude, station.longitude, max_corridor_miles=self.corridor_radius_miles
            )
            position = along * scale
            if off_route <= self.corridor_radius_miles and 0.0 < position < total_distance_miles:
                candidates.append(
                    CandidateStation(position_miles=position, price=float(station.retail_price), payload=station)
                )
        return candidates

    def _serialize(self, plan: FuelPlan, total_distance_miles: float, note: str) -> Dict[str, Any]:
        stops: List[Dict[str, Any]] = []
        previous_position = 0.0
        for number, purchase in enumerate(plan.purchases, start=1):
            st = purchase.station.payload
            position = purchase.station.position_miles
            stops.append({
                "stop_number": number,
                "station_id": st.opis_id,
                "name": st.name,
                "address": st.address,
                "city": st.city,
                "state": st.state,
                "latitude": round(st.latitude, 6),
                "longitude": round(st.longitude, 6),
                "price_per_gallon": round(purchase.station.price, 4),
                "distance_from_start_miles": round(position, 2),
                "distance_from_previous_stop_miles": round(position - previous_position, 2),
                "fuel_on_arrival_gallons": round(purchase.fuel_on_arrival_gallons, 2),
                "fuel_purchased_gallons": round(purchase.gallons, 2),
                "fuel_after_purchase_gallons": round(purchase.fuel_after_purchase_gallons, 2),
                "cost": round(purchase.cost, 2),
            })
            previous_position = position

        return {
            "stops": stops,
            "starting_fuel_gallons": round(plan.starting_fuel_gallons, 2),
            "total_fuel_consumed_gallons": round(plan.total_consumed_gallons, 2),
            "total_fuel_purchased_gallons": round(plan.total_purchased_gallons, 2),
            "ending_fuel_gallons": round(plan.ending_fuel_gallons, 2),
            "distance_from_last_stop_to_destination_miles": round(total_distance_miles - previous_position, 2),
            "total_fuel_cost": round(sum(s["cost"] for s in stops), 2),
            "assumptions_note": note,
        }
