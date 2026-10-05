"""
Mathematical tests for the pure fuel-planning algorithm.

The greedy is cross-checked against an exhaustive dynamic program over
(station, fuel-in-tank) states, discretised to 1 mile of fuel. With integer
station positions, an optimal plan only ever buys whole miles of fuel, so the
discretised DP gives the exact optimum.
"""

import random

from django.test import SimpleTestCase

from routing.services.fuel_optimizer import (
    CandidateStation,
    NoFeasibleFuelPlanError,
    compute_optimal_fuel_plan,
)

RANGE = 500.0
MPG = 10.0
CAPACITY = RANGE / MPG


def plan_for(stations, distance, max_range=RANGE, mpg=MPG):
    candidates = [CandidateStation(position_miles=p, price=c, payload=name) for name, p, c in stations]
    return compute_optimal_fuel_plan(candidates, distance, max_range, mpg)


def purchases_by_name(plan):
    return {p.station.payload: round(p.gallons, 6) for p in plan.purchases}


def brute_force_min_cost(stations, distance, max_range, mpg):
    """Exact fuel-state DP. Returns None when infeasible."""
    rng = int(max_range)
    inf = float("inf")
    nodes = sorted(stations, key=lambda s: s[0]) + [(distance, None)]
    dp = [inf] * (rng + 1)
    dp[rng] = 0.0  # start full, free
    position = 0
    for node_pos, price in nodes:
        gap = node_pos - position
        moved = [inf] * (rng + 1)
        for f in range(gap, rng + 1):
            moved[f - gap] = dp[f]
        dp = moved
        position = node_pos
        if price is None:
            break
        per_mile = price / mpg
        for f in range(1, rng + 1):  # buy fuel one mile at a time
            dp[f] = min(dp[f], dp[f - 1] + per_mile)
    best = min(dp)
    return None if best == inf else best


class FuelPlanAccountingMixin:
    def assert_valid_plan(self, plan, distance, max_range=RANGE, mpg=MPG):
        capacity = max_range / mpg
        # initial + purchased == consumed + ending
        self.assertAlmostEqual(
            plan.starting_fuel_gallons + plan.total_purchased_gallons,
            plan.total_consumed_gallons + plan.ending_fuel_gallons,
            places=6,
        )
        self.assertAlmostEqual(plan.total_consumed_gallons, distance / mpg, places=6)
        self.assertAlmostEqual(plan.total_cost, sum(p.gallons * p.station.price for p in plan.purchases), places=6)
        previous = 0.0
        for p in plan.purchases:
            self.assertLessEqual(p.station.position_miles - previous, max_range + 1e-6)
            self.assertGreaterEqual(p.fuel_on_arrival_gallons, -1e-9)
            self.assertLessEqual(p.fuel_after_purchase_gallons, capacity + 1e-6)
            self.assertGreater(p.gallons, 0.0)
            previous = p.station.position_miles
        self.assertLessEqual(distance - previous, max_range + 1e-6)


class ComputeOptimalFuelPlanTest(FuelPlanAccountingMixin, SimpleTestCase):

    def test_short_route_uses_starting_fuel_only(self):
        plan = plan_for([("A", 50, 2.0)], 100)
        self.assertEqual(plan.purchases, [])
        self.assertAlmostEqual(plan.total_consumed_gallons, 10.0)
        self.assertAlmostEqual(plan.total_purchased_gallons, 0.0)
        self.assertAlmostEqual(plan.total_cost, 0.0)
        self.assertAlmostEqual(plan.ending_fuel_gallons, 40.0)

    def test_exactly_500_miles_needs_no_stop(self):
        plan = plan_for([("A", 250, 3.0)], 500)
        self.assertEqual(plan.purchases, [])
        self.assertAlmostEqual(plan.ending_fuel_gallons, 0.0)

    def test_600_mile_route_buys_only_missing_10_gallons(self):
        plan = plan_for([("A", 450, 3.0)], 600)
        self.assertEqual(purchases_by_name(plan), {"A": 10.0})
        self.assertAlmostEqual(plan.purchases[0].fuel_on_arrival_gallons, 5.0)
        self.assertAlmostEqual(plan.total_cost, 30.0)
        self.assertAlmostEqual(plan.ending_fuel_gallons, 0.0)
        self.assert_valid_plan(plan, 600)

    def test_skips_unnecessary_expensive_early_station(self):
        plan = plan_for([("EARLY", 50, 4.0), ("LATER", 400, 3.0)], 800)
        self.assertEqual(purchases_by_name(plan), {"LATER": 30.0})
        self.assertAlmostEqual(plan.total_cost, 90.0)
        self.assert_valid_plan(plan, 800)

    def test_does_not_pick_cheapest_or_nearest_blindly(self):
        # Spec example: A=100mi $5, B=300mi $2, C=450mi $6.
        plan = plan_for([("A", 100, 5.0), ("B", 300, 2.0), ("C", 450, 6.0)], 700)
        self.assertEqual(purchases_by_name(plan), {"B": 20.0})
        self.assertAlmostEqual(plan.total_cost, 40.0)
        self.assert_valid_plan(plan, 700)

    def test_carries_fuel_from_cheap_station_past_expensive_one(self):
        plan = plan_for([("CHEAP", 300, 2.0), ("PRICEY", 600, 5.0)], 1000)
        # Fill up at CHEAP (30 gal), then only 20 gal at PRICEY.
        self.assertEqual(purchases_by_name(plan), {"CHEAP": 30.0, "PRICEY": 20.0})
        self.assertAlmostEqual(plan.total_cost, 160.0)  # naive "just enough" would cost 220
        self.assert_valid_plan(plan, 1000)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        plan = plan_for([("PRICEY", 400, 5.0), ("CHEAP", 600, 2.0)], 1000)
        # Arrive at PRICEY with 10 gal, need 20 to reach CHEAP -> buy 10.
        self.assertEqual(purchases_by_name(plan), {"PRICEY": 10.0, "CHEAP": 40.0})
        self.assertAlmostEqual(plan.total_cost, 130.0)
        self.assert_valid_plan(plan, 1000)

    def test_final_leg_buys_only_what_destination_requires(self):
        plan = plan_for([("A", 300, 3.0), ("B", 700, 3.5)], 900)
        self.assertAlmostEqual(plan.ending_fuel_gallons, 0.0)
        self.assertAlmostEqual(plan.total_purchased_gallons, 90.0 - 50.0)
        self.assert_valid_plan(plan, 900)

    def test_multiple_stops_on_long_route(self):
        stations = [(f"S{m}", m, 3.0 + (m % 7) * 0.1) for m in range(150, 2800, 150)]
        plan = plan_for(stations, 2800)
        self.assertGreaterEqual(len(plan.purchases), 5)
        self.assertAlmostEqual(plan.total_purchased_gallons, 280.0 - 50.0, places=6)
        self.assert_valid_plan(plan, 2800)

    def test_impossible_gap_raises(self):
        with self.assertRaises(NoFeasibleFuelPlanError):
            plan_for([("FAR", 600, 3.0)], 900)
        with self.assertRaises(NoFeasibleFuelPlanError):
            plan_for([("A", 400, 3.0), ("B", 950, 3.0)], 1200)

    def test_matches_exhaustive_fuel_state_dp(self):
        rnd = random.Random(42)
        max_range, mpg = 100.0, 10.0
        checked_feasible = 0
        for _ in range(400):
            distance = rnd.randint(101, 450)
            stations = [
                (rnd.randint(1, distance - 1), round(rnd.uniform(2.5, 6.0), 2))
                for _ in range(rnd.randint(1, 10))
            ]
            expected = brute_force_min_cost(stations, distance, max_range, mpg)
            named = [(f"S{i}", p, c) for i, (p, c) in enumerate(stations)]
            if expected is None:
                with self.assertRaises(NoFeasibleFuelPlanError):
                    plan_for(named, distance, max_range, mpg)
                continue
            plan = plan_for(named, distance, max_range, mpg)
            self.assertAlmostEqual(plan.total_cost, expected, places=6, msg=f"{stations} D={distance}")
            self.assert_valid_plan(plan, distance, max_range, mpg)
            checked_feasible += 1
        self.assertGreater(checked_feasible, 50)
