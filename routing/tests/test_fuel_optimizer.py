from decimal import Decimal
from django.test import TestCase
from routing.models import FuelStation
from routing.services.fuel_optimizer import (
    FuelOptimizer,
    NoFeasibleFuelPlanError,
)


class FuelOptimizerTest(TestCase):
    """Unit and algorithmic tests for FuelOptimizer."""

    def setUp(self):
        self.optimizer = FuelOptimizer(max_range_miles=500.0, mpg=10.0, corridor_radius_miles=15.0)

    def test_short_route_under_500_miles(self):
        # 300-mile route from lat 40.0, lon -80.0 to lat 40.0, lon -74.0 (~315 miles)
        geometry = {
            "type": "LineString",
            "coordinates": [
                [-80.0, 40.0],
                [-74.0, 40.0],
            ],
        }
        total_distance = 315.0

        plan = self.optimizer.optimize(geometry, total_distance)

        self.assertEqual(len(plan["stops"]), 0)
        self.assertAlmostEqual(plan["total_fuel_consumed_gallons"], 31.5)
        self.assertEqual(plan["total_fuel_purchased_gallons"], 0.0)
        self.assertEqual(plan["total_fuel_cost"], 0.0)
        self.assertIn("Departing with a full tank", plan["assumptions_note"])

    def test_multi_stop_route_optimization(self):
        """
        Route of 1200 miles with multiple candidate stations.
        Start at lon -90.0, end at lon -70.0 (~1050 miles along lat 40.0).
        Let's set up explicit coordinates so total_distance is ~1100 miles.
        """
        route_coords = [
            [-90.0, 40.0],  # Mile 0
            [-85.0, 40.0],  # ~260 mi
            [-80.0, 40.0],  # ~520 mi
            [-75.0, 40.0],  # ~780 mi
            [-70.0, 40.0],  # ~1040 mi
        ]
        geometry = {"type": "LineString", "coordinates": route_coords}
        total_distance = 1040.0

        # Create candidate stations along the route
        # Station 1: at lon -85.0 (~260 mi) - Expensive ($4.00)
        FuelStation.objects.create(
            opis_id=1,
            name="Station Expensive 1",
            city="City 1",
            state="IL",
            retail_price=Decimal("4.00"),
            latitude=40.01,
            longitude=-85.0,
        )
        # Station 2: at lon -84.0 (~315 mi) - Cheap ($3.00)
        FuelStation.objects.create(
            opis_id=2,
            name="Station Cheap 2",
            city="City 2",
            state="IN",
            retail_price=Decimal("3.00"),
            latitude=40.01,
            longitude=-84.0,
        )
        # Station 3: at lon -76.0 (~730 mi) - Cheap ($3.10)
        FuelStation.objects.create(
            opis_id=3,
            name="Station Cheap 3",
            city="City 3",
            state="PA",
            retail_price=Decimal("3.10"),
            latitude=40.01,
            longitude=-76.0,
        )

        plan = self.optimizer.optimize(geometry, total_distance)

        stops = plan["stops"]
        self.assertGreater(len(stops), 0)

        # Ensure no individual driving leg exceeds 500 miles
        prev_d = 0.0
        for stop in stops:
            leg = stop["distance_from_start_miles"] - prev_d
            self.assertLessEqual(leg, 500.0)
            prev_d = stop["distance_from_start_miles"]

        final_leg = total_distance - prev_d
        self.assertLessEqual(final_leg, 500.0)

        # Cheaper station (Station 2 at $3.00) should be preferred over Station 1 at $4.00
        stop_ids = [s["station_id"] for s in stops]
        self.assertIn(2, stop_ids)
        self.assertNotIn(1, stop_ids)

        # Verify math
        self.assertAlmostEqual(plan["total_fuel_consumed_gallons"], 104.0)
        expected_purchased = sum(s["fuel_purchased_gallons"] for s in stops)
        self.assertAlmostEqual(plan["total_fuel_purchased_gallons"], expected_purchased, places=1)
        expected_cost = sum(s["cost"] for s in stops)
        self.assertAlmostEqual(plan["total_fuel_cost"], expected_cost)

        # initial + purchased == consumed + ending (starting tank is free)
        self.assertEqual(plan["starting_fuel_gallons"], 50.0)
        self.assertAlmostEqual(
            plan["starting_fuel_gallons"] + plan["total_fuel_purchased_gallons"],
            plan["total_fuel_consumed_gallons"] + plan["ending_fuel_gallons"],
            places=1,
        )
        self.assertAlmostEqual(plan["total_fuel_purchased_gallons"], 104.0 - 50.0, places=1)

    def test_impossible_route_gap_raises_error(self):
        """
        Route of 1200 miles with NO stations between mile 0 and mile 700 (> 500 miles).
        Must raise NoFeasibleFuelPlanError.
        """
        route_coords = [
            [-90.0, 40.0],
            [-70.0, 40.0],
        ]
        geometry = {"type": "LineString", "coordinates": route_coords}
        total_distance = 1040.0

        # Only one station at lon -73.0 (~880 mi), meaning gap from 0 to 880 mi is impossible
        FuelStation.objects.create(
            opis_id=99,
            name="Too Far Station",
            city="FarCity",
            state="NJ",
            retail_price=Decimal("3.50"),
            latitude=40.01,
            longitude=-73.0,
        )

        with self.assertRaises(NoFeasibleFuelPlanError):
            self.optimizer.optimize(geometry, total_distance)
